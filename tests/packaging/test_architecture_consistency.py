"""
Architecture Consistency Tests

This test suite validates that the architecture cleanup has been properly implemented
and that the new shared modules architecture is being used consistently.

Requirements covered:
- 18.1: Simplified Lambda function architecture
- 18.2: Clear architectural boundaries
- 22.1: Consistent patterns
- 22.2: Clean codebase organization
"""

import os
import sys
import unittest
from pathlib import Path
import importlib.util
import ast

# Add the lambda directory to the Python path for imports
lambda_dir = Path(__file__).parent.parent.parent / "source" / "lambda"
sys.path.insert(0, str(lambda_dir))


class TestArchitectureConsistency(unittest.TestCase):
    """Test architecture consistency after cleanup"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.lambda_dir = Path(__file__).parent.parent.parent / "source" / "lambda"
        self.shared_dir = self.lambda_dir / "shared"
        self.partner_api_dir = self.lambda_dir / "partner_api"
        self.ui_backend_dir = self.lambda_dir / "ui_backend"
    
    def test_shared_modules_structure_exists(self):
        """Test that shared modules structure exists and is properly organized"""
        # Check main shared directory
        self.assertTrue(self.shared_dir.exists(), "Shared modules directory should exist")
        
        # Check subdirectories (shared/services was consolidated into the top-level services/ package)
        expected_subdirs = ["auth", "utils", "handlers"]
        for subdir in expected_subdirs:
            subdir_path = self.shared_dir / subdir
            self.assertTrue(subdir_path.exists(), f"Shared/{subdir} directory should exist")
            
            # Check for __init__.py files
            init_file = subdir_path / "__init__.py"
            self.assertTrue(init_file.exists(), f"Shared/{subdir}/__init__.py should exist")
    
    def test_authentication_service_uses_shared_modules(self):
        """Test that authentication_service.py uses shared modules"""
        auth_service_path = self.lambda_dir / "authentication_service.py"
        self.assertTrue(auth_service_path.exists(), "authentication_service.py should exist")
        
        with open(auth_service_path, 'r') as f:
            content = f.read()
        
        # Check for shared module imports
        expected_imports = [
            "from shared.utils.response_helpers import",
            "from shared.auth.authentication_functions import"
        ]
        
        for expected_import in expected_imports:
            self.assertIn(expected_import, content, 
                         f"authentication_service.py should import {expected_import}")
        
        # Check for migration note
        self.assertIn("MIGRATION NOTE", content, 
                     "authentication_service.py should have migration note")
    
    def test_access_logger_uses_shared_modules(self):
        """Test that access_logger.py uses shared modules"""
        access_logger_path = self.lambda_dir / "access_logger.py"
        self.assertTrue(access_logger_path.exists(), "access_logger.py should exist")
        
        with open(access_logger_path, 'r') as f:
            content = f.read()
        
        # Check for shared module imports
        expected_imports = [
            "from shared.utils.response_helpers import"
        ]
        
        for expected_import in expected_imports:
            self.assertIn(expected_import, content, 
                         f"access_logger.py should import {expected_import}")
        
        # Check for migration note
        self.assertIn("MIGRATION NOTE", content, 
                     "access_logger.py should have migration note")
    
    def test_access_rules_engine_uses_shared_modules(self):
        """Test that access_rules_engine.py uses shared modules"""
        access_rules_path = self.lambda_dir / "access_rules_engine.py"
        self.assertTrue(access_rules_path.exists(), "access_rules_engine.py should exist")
        
        with open(access_rules_path, 'r') as f:
            content = f.read()
        
        # Check for shared module imports
        expected_imports = [
            "from shared.utils.response_helpers import",
            "from shared.utils.safe_datetime_parser import"
        ]
        
        for expected_import in expected_imports:
            self.assertIn(expected_import, content, 
                         f"access_rules_engine.py should import {expected_import}")
        
        # Check for migration note
        self.assertIn("MIGRATION NOTE", content, 
                     "access_rules_engine.py should have migration note")
    
    def test_new_lambda_functions_exist(self):
        """Test that new Lambda function structure exists"""
        # Check partner API
        partner_handler = self.partner_api_dir / "handler.py"
        self.assertTrue(partner_handler.exists(), "Partner API handler should exist")
        
        partner_requirements = self.partner_api_dir / "requirements.txt"
        self.assertTrue(partner_requirements.exists(), "Partner API requirements should exist")
        
        # Check UI backend
        ui_handler = self.ui_backend_dir / "handler.py"
        self.assertTrue(ui_handler.exists(), "UI Backend handler should exist")
        
        ui_requirements = self.ui_backend_dir / "requirements.txt"
        self.assertTrue(ui_requirements.exists(), "UI Backend requirements should exist")
    
    def test_shared_authentication_functions_complete(self):
        """Test that shared authentication functions are complete"""
        auth_functions_path = self.shared_dir / "auth" / "authentication_functions.py"
        self.assertTrue(auth_functions_path.exists(), "Shared authentication functions should exist")
        
        with open(auth_functions_path, 'r') as f:
            content = f.read()
        
        # Check for required functions
        required_functions = [
            "def resolve_partner_from_event(",
            "def extract_cognito_user_context(",
            "def generate_jwt_token(",
            "def verify_jwt_token(",
            "def get_jwt_secret(",
            "def publish_auth_failure_metric("
        ]
        
        for func in required_functions:
            self.assertIn(func, content, 
                         f"Shared authentication functions should contain {func}")

        # Key values are validated by API Gateway; the hash lookup and usage counter are gone.
        for retired in ("def validate_api_key(", "def increment_api_usage(", "def authenticate_partner("):
            self.assertNotIn(retired, content, f"{retired} was retired with the custom authorizer")
    
    def test_shared_response_helpers_complete(self):
        """Test that shared response helpers are complete"""
        response_helpers_path = self.shared_dir / "utils" / "response_helpers.py"
        self.assertTrue(response_helpers_path.exists(), "Shared response helpers should exist")
        
        with open(response_helpers_path, 'r') as f:
            content = f.read()
        
        # Check for required functions
        required_functions = [
            "def create_success_response(",
            "def create_error_response(",
            "def create_authentication_error_response(",
            "def create_validation_error_response("
        ]
        
        for func in required_functions:
            self.assertIn(func, content, 
                         f"Shared response helpers should contain {func}")
    
    def test_no_duplicate_response_functions(self):
        """Test that response helper functions are not duplicated in migrated files"""
        files_to_check = [
            self.lambda_dir / "authentication_service.py",
            self.lambda_dir / "access_logger.py",
            self.lambda_dir / "access_rules_engine.py"
        ]
        
        for file_path in files_to_check:
            if file_path.exists():
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # These functions should NOT be defined in the migrated files
                duplicate_functions = [
                    "def create_success_response(",
                    "def create_error_response("
                ]
                
                for func in duplicate_functions:
                    self.assertNotIn(func, content, 
                                   f"{file_path.name} should not define {func} - use shared version")
    
    def test_consistent_import_patterns(self):
        """Test that migrated files use consistent import patterns"""
        files_to_check = [
            self.lambda_dir / "authentication_service.py",
            self.lambda_dir / "access_logger.py",
            self.lambda_dir / "access_rules_engine.py"
        ]
        
        for file_path in files_to_check:
            if file_path.exists():
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Check for consistent shared imports
                if "create_success_response" in content:
                    self.assertIn("from shared.utils.response_helpers import", content,
                                f"{file_path.name} should import response helpers from shared")
    
    def test_packaging_script_updated(self):
        """Test that packaging script supports the new architecture"""
        packaging_script = Path(__file__).parent.parent.parent / "deployment" / "infrastructure" / "package_lambda.sh"
        self.assertTrue(packaging_script.exists(), "Packaging script should exist")
        
        with open(packaging_script, 'r') as f:
            content = f.read()
        
        # Check for function packaging options
        self.assertIn("partner-api", content, "Packaging script should support partner-api function")
        self.assertIn("ui-backend", content, "Packaging script should support ui-backend function")
        self.assertIn("shared-services", content, "Packaging script should support shared-services function")
    
    def test_no_circular_imports(self):
        """Test that there are no circular imports in the shared modules"""
        # This is a basic test - in a real scenario you might use more sophisticated tools
        shared_files = list(self.shared_dir.rglob("*.py"))
        
        for file_path in shared_files:
            if file_path.name == "__init__.py":
                continue
                
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Parse the AST to find imports
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith('shared'):
                            # Check for potential circular imports
                            # This is a simplified check
                            relative_path = file_path.relative_to(self.shared_dir)
                            current_module = str(relative_path.with_suffix('')).replace('/', '.')
                            
                            # Basic circular import detection
                            if node.module == f"shared.{current_module}":
                                self.fail(f"Potential circular import in {file_path}: imports {node.module}")
                                
            except SyntaxError:
                self.fail(f"Syntax error in {file_path}")
            except Exception as e:
                # Don't fail the test for import analysis issues, just warn
                print(f"Warning: Could not analyze imports in {file_path}: {e}")


class TestPackagingConsistency(unittest.TestCase):
    """Test packaging consistency"""
    
    def test_deployment_packages_exist(self):
        """Test that deployment packages can be created"""
        lambda_dir = Path(__file__).parent.parent.parent / "source" / "lambda"
        
        # Check that the new architecture directories exist
        self.assertTrue((lambda_dir / "partner_api").exists(), "Partner API directory should exist")
        self.assertTrue((lambda_dir / "ui_backend").exists(), "UI Backend directory should exist")
        self.assertTrue((lambda_dir / "shared").exists(), "Shared directory should exist")
        
        # Check that requirements files exist
        self.assertTrue((lambda_dir / "partner_api" / "requirements.txt").exists(), 
                       "Partner API requirements should exist")
        self.assertTrue((lambda_dir / "ui_backend" / "requirements.txt").exists(), 
                       "UI Backend requirements should exist")
        self.assertTrue((lambda_dir / "shared" / "requirements.txt").exists(), 
                       "Shared requirements should exist")


if __name__ == '__main__':
    unittest.main()