"""
Function signature migration utilities

This module provides utilities to help migrate existing Lambda handlers
to use standardized function signatures: (headers, request_data, event, **kwargs)
"""

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class SignatureMigrationHelper:
    """
    Helper class for migrating function signatures to the standardized format
    """

    # Standard signature pattern
    STANDARD_SIGNATURE = (
        "(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]"
    )

    # Common signature patterns to detect and migrate
    SIGNATURE_PATTERNS = [
        # Pattern 1: (headers, query_params, **kwargs)
        {
            "pattern": r"def\s+(\w+)\s*\(\s*headers:\s*Dict\[str,\s*str\]\s*,\s*query_params:\s*Dict\[str,\s*str\]\s*,?\s*\*\*kwargs\s*\)",
            "replacement": r"def \1(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs)",
            "body_changes": [("query_params", "request_data")],
        },
        # Pattern 2: (headers, request_data, **kwargs) - missing event
        {
            "pattern": r"def\s+(\w+)\s*\(\s*headers:\s*Dict\[str,\s*str\]\s*,\s*request_data:\s*Dict\[str,\s*Any\]\s*,?\s*\*\*kwargs\s*\)",
            "replacement": r"def \1(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs)",
            "body_changes": [],
        },
        # Pattern 3: (headers, event, context) - bot control pattern
        {
            "pattern": r"def\s+(\w+)\s*\(\s*headers:\s*Dict\[str,\s*str\]\s*,\s*event:\s*Dict\[str,\s*Any\]\s*,\s*context:\s*Any\s*\)",
            "replacement": r"def \1(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs)",
            "body_changes": [("context", 'kwargs.get("context")')],
        },
        # Pattern 4: (*args, **kwargs) - needs complete rewrite
        {
            "pattern": r"def\s+(\w+)\s*\(\s*\*args\s*,\s*\*\*kwargs\s*\)",
            "replacement": r"def \1(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs)",
            "body_changes": [("args[0]", "headers"), ("args[1]", "request_data"), ("args[2]", "event")],
        },
    ]

    def analyze_function_signatures(self, file_content: str) -> List[Dict[str, Any]]:
        """
        Analyze function signatures in a file and identify migration needs

        Args:
            file_content: Content of the Python file to analyze

        Returns:
            List of analysis results for each function
        """
        results = []

        # Find all function definitions
        function_pattern = r"def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^:]+)?:"
        functions = re.finditer(function_pattern, file_content, re.MULTILINE)

        for match in functions:
            function_name = match.group(1)
            function_def = match.group(0)

            # Skip private functions and special methods
            if function_name.startswith("_") or function_name in ["lambda_handler"]:
                continue

            # Check if it matches standard signature
            is_standard = self.STANDARD_SIGNATURE.replace("(", r"\(").replace(")", r"\)") in function_def

            # Find matching pattern
            matching_pattern = None
            for pattern_info in self.SIGNATURE_PATTERNS:
                if re.search(pattern_info["pattern"], function_def):
                    matching_pattern = pattern_info
                    break

            results.append(
                {
                    "function_name": function_name,
                    "current_signature": function_def,
                    "is_standard": is_standard,
                    "matching_pattern": matching_pattern,
                    "needs_migration": not is_standard and matching_pattern is not None,
                }
            )

        return results

    def migrate_function_signature(self, file_content: str, function_name: str, pattern_info: Dict[str, Any]) -> str:
        """
        Migrate a specific function signature in file content

        Args:
            file_content: Content of the Python file
            function_name: Name of the function to migrate
            pattern_info: Pattern information for migration

        Returns:
            Updated file content
        """
        # Replace function signature
        updated_content = re.sub(pattern_info["pattern"], pattern_info["replacement"], file_content)

        # Apply body changes if needed
        for old_var, new_var in pattern_info.get("body_changes", []):
            # Only replace within the function body (this is a simplified approach)
            updated_content = updated_content.replace(old_var, new_var)

        return updated_content

    def generate_migration_report(self, file_path: str, file_content: str) -> str:
        """
        Generate a migration report for a file

        Args:
            file_path: Path to the file being analyzed
            file_content: Content of the file

        Returns:
            Migration report as a string
        """
        analysis = self.analyze_function_signatures(file_content)

        report = [f"Migration Report for {file_path}"]
        report.append("=" * 50)

        standard_count = sum(1 for result in analysis if result["is_standard"])
        needs_migration_count = sum(1 for result in analysis if result["needs_migration"])

        report.append(f"Total functions analyzed: {len(analysis)}")
        report.append(f"Functions with standard signature: {standard_count}")
        report.append(f"Functions needing migration: {needs_migration_count}")
        report.append("")

        if needs_migration_count > 0:
            report.append("Functions needing migration:")
            report.append("-" * 30)

            for result in analysis:
                if result["needs_migration"]:
                    report.append(f"Function: {result['function_name']}")
                    report.append(f"Current: {result['current_signature']}")
                    report.append(f"Pattern: {result['matching_pattern']['pattern']}")
                    report.append("")

        return "\n".join(report)


def create_standardized_handler_template(handler_name: str, auth_decorator: str = "require_cognito_auth") -> str:
    """
    Create a template for a standardized handler function

    Args:
        handler_name: Name of the handler function
        auth_decorator: Authentication decorator to use

    Returns:
        Template code for the handler
    """
    template = f'''
@{auth_decorator}
def {handler_name}(headers: Dict[str, str], request_data: Dict[str, Any], 
                  event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """
    Handle {handler_name.replace("handle_", "").replace("_", " ")} endpoint
    
    Args:
        headers: HTTP headers
        request_data: Request body data or query parameters
        event: API Gateway event
        **kwargs: Additional arguments including authenticated_user
        
    Returns:
        API response dictionary
    """
    logger.info(f"=== {{handler_name.upper()}} START ===")
    
    try:
        user = kwargs['authenticated_user']
        auth_type = kwargs.get('auth_type', 'unknown')
        
        # Validate required fields
        required_fields = []  # Add required fields here
        validation_errors = {{}}
        
        for field in required_fields:
            if field not in request_data:
                validation_errors[field] = f"Field '{{field}}' is required"
        
        if validation_errors:
            return create_validation_error_response(validation_errors)
        
        # TODO: Implement handler logic here
        
        result = {{
            'status': 'success',
            'message': 'Handler implemented successfully'
        }}
        
        logger.info(f"=== {{handler_name.upper()}} END === Success")
        
        return create_success_response(result)
        
    except Exception as e:
        logger.error(f"=== {{handler_name.upper()}} ERROR === {{str(e)}}")
        return create_error_response(500, f"Failed to process {{handler_name.replace('handle_', '').replace('_', ' ')}}")
'''

    return template.strip()


def validate_handler_signature(handler_func) -> Tuple[bool, str]:
    """
    Validate that a handler function has the correct signature

    Args:
        handler_func: Handler function to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    import inspect

    try:
        sig = inspect.signature(handler_func)
        params = list(sig.parameters.keys())

        # Check required parameters
        required_params = ["headers", "request_data", "event"]

        if len(params) < len(required_params):
            return False, f"Missing required parameters. Expected: {required_params}, Got: {params}"

        # Check parameter names (first 3 should match)
        for i, required_param in enumerate(required_params):
            if i >= len(params) or params[i] != required_param:
                return (
                    False,
                    f"Parameter {i + 1} should be '{required_param}', got '{params[i] if i < len(params) else 'missing'}'",
                )

        # Check for **kwargs
        if not any(param.kind == param.VAR_KEYWORD for param in sig.parameters.values()):
            return False, "Missing **kwargs parameter"

        return True, "Signature is valid"

    except Exception as e:
        return False, f"Error validating signature: {str(e)}"
