"""
Test folder structure migration validation

Tests to ensure the lambda folder reorganization is correct and all
required files are in the proper locations.
"""

import os
import pytest
from pathlib import Path

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent

class TestLambdaFolderStructure:
    """Test the new lambda folder structure"""
    
    def test_partner_api_structure(self):
        """Test that partner_api directory has required files"""
        partner_api_dir = PROJECT_ROOT / "source" / "lambda" / "partner_api"
        
        assert partner_api_dir.exists(), "partner_api directory should exist"
        assert (partner_api_dir / "__init__.py").exists(), "partner_api should have __init__.py"
        assert (partner_api_dir / "handler.py").exists(), "partner_api should have handler.py"
        assert (partner_api_dir / "requirements.txt").exists(), "partner_api should have requirements.txt"
    
    def test_ui_backend_structure(self):
        """Test that ui_backend directory has required files"""
        ui_backend_dir = PROJECT_ROOT / "source" / "lambda" / "ui_backend"
        
        assert ui_backend_dir.exists(), "ui_backend directory should exist"
        assert (ui_backend_dir / "__init__.py").exists(), "ui_backend should have __init__.py"
        assert (ui_backend_dir / "handler.py").exists(), "ui_backend should have handler.py"
        assert (ui_backend_dir / "requirements.txt").exists(), "ui_backend should have requirements.txt"
    
    def test_shared_structure(self):
        """Test that shared directory has required subdirectories and files"""
        shared_dir = PROJECT_ROOT / "source" / "lambda" / "shared"
        
        assert shared_dir.exists(), "shared directory should exist"
        assert (shared_dir / "__init__.py").exists(), "shared should have __init__.py"
        
        # Test auth subdirectory
        auth_dir = shared_dir / "auth"
        assert auth_dir.exists(), "shared/auth directory should exist"
        assert (auth_dir / "__init__.py").exists(), "shared/auth should have __init__.py"
        assert (auth_dir / "authentication_decorators.py").exists(), "shared/auth should have authentication_decorators.py"
        assert (auth_dir / "authentication_functions.py").exists(), "shared/auth should have authentication_functions.py"
        
        # Test utils subdirectory
        utils_dir = shared_dir / "utils"
        assert utils_dir.exists(), "shared/utils directory should exist"
        assert (utils_dir / "__init__.py").exists(), "shared/utils should have __init__.py"
        assert (utils_dir / "response_helpers.py").exists(), "shared/utils should have response_helpers.py"
        assert (utils_dir / "feature_flags.py").exists(), "shared/utils should have feature_flags.py"
        assert (utils_dir / "response_validator.py").exists(), "shared/utils should have response_validator.py"
        assert (utils_dir / "safe_datetime_parser.py").exists(), "shared/utils should have safe_datetime_parser.py"
        
        # Test handlers subdirectory (decomposition placed the internal-log handler here;
        # the former shared/services was consolidated into the top-level services/ package)
        handlers_dir = shared_dir / "handlers"
        assert handlers_dir.exists(), "shared/handlers directory should exist"
        assert (handlers_dir / "__init__.py").exists(), "shared/handlers should have __init__.py"
    
    def test_shared_requirements(self):
        """Test that shared directory has requirements.txt"""
        shared_requirements = PROJECT_ROOT / "source" / "lambda" / "shared" / "requirements.txt"
        assert shared_requirements.exists(), "shared should have requirements.txt"

class TestImportStructure:
    """Test that imports work correctly with the new structure"""
    
    def test_shared_auth_imports(self):
        """Test that shared auth modules can be imported"""
        import sys
        import os
        
        # Add the lambda directory to the path
        lambda_dir = str(PROJECT_ROOT / "source" / "lambda")
        if lambda_dir not in sys.path:
            sys.path.insert(0, lambda_dir)
        
        try:
            from shared.auth import (
                require_partner_auth,
                require_cognito_auth,
                require_partner_or_cognito_auth,
                optional_auth
            )
            # If we get here, imports worked
            assert True
        except ImportError as e:
            pytest.fail(f"Failed to import shared auth modules: {e}")
    
    def test_shared_utils_imports(self):
        """Test that shared utils modules can be imported"""
        import sys
        
        # Add the lambda directory to the path
        lambda_dir = str(PROJECT_ROOT / "source" / "lambda")
        if lambda_dir not in sys.path:
            sys.path.insert(0, lambda_dir)
        
        try:
            from shared.utils import (
                create_success_response,
                create_error_response,
            )
            # If we get here, imports worked
            assert True
        except ImportError as e:
            pytest.fail(f"Failed to import shared utils modules: {e}")

class TestRequirementsFiles:
    """Test that requirements files are properly configured"""
    
    def test_partner_api_requirements(self):
        """Test partner_api requirements.txt content"""
        requirements_file = PROJECT_ROOT / "source" / "lambda" / "partner_api" / "requirements.txt"
        
        assert requirements_file.exists(), "partner_api requirements.txt should exist"
        
        content = requirements_file.read_text()
        assert "PyYAML" in content, "partner_api requirements should include PyYAML"
        assert "boto3" in content, "partner_api requirements should include boto3"
    
    def test_ui_backend_requirements(self):
        """Test ui_backend requirements.txt content"""
        requirements_file = PROJECT_ROOT / "source" / "lambda" / "ui_backend" / "requirements.txt"
        
        assert requirements_file.exists(), "ui_backend requirements.txt should exist"
        
        content = requirements_file.read_text()
        assert "PyYAML" in content, "ui_backend requirements should include PyYAML"
        assert "boto3" in content, "ui_backend requirements should include boto3"
    
    def test_shared_requirements(self):
        """Test shared requirements.txt content"""
        requirements_file = PROJECT_ROOT / "source" / "lambda" / "shared" / "requirements.txt"
        
        assert requirements_file.exists(), "shared requirements.txt should exist"
        
        content = requirements_file.read_text()
        assert "PyYAML" in content, "shared requirements should include PyYAML"
        assert "boto3" in content, "shared requirements should include boto3"

class TestHandlerFiles:
    """Test that handler files have correct structure"""
    
    def test_partner_api_handler_structure(self):
        """Test partner_api handler.py has required functions"""
        handler_file = PROJECT_ROOT / "source" / "lambda" / "partner_api" / "handler.py"
        
        assert handler_file.exists(), "partner_api handler.py should exist"
        
        content = handler_file.read_text()
        
        # Check for required functions
        assert "def lambda_handler(" in content, "partner_api handler should have lambda_handler function"
        assert "def handle_info_request(" in content, "partner_api handler should have handle_info_request function"
        assert "def handle_taxonomy_request(" in content, "partner_api handler should have handle_taxonomy_request function"
        assert "def handle_query_request(" in content, "partner_api handler should have handle_query_request function"
        
        # Check for required imports
        assert "from shared.auth import" in content, "partner_api handler should import from shared.auth"
        assert "from shared.utils import" in content, "partner_api handler should import from shared.utils"
    
    def test_ui_backend_handler_structure(self):
        """Test ui_backend handler.py has required functions"""
        handler_file = PROJECT_ROOT / "source" / "lambda" / "ui_backend" / "handler.py"
        
        assert handler_file.exists(), "ui_backend handler.py should exist"
        
        content = handler_file.read_text()
        
        # Check for the thin-dispatcher shape: a lambda_handler that delegates to the
        # routes/ package (the monolithic handle_*_endpoints were removed at the dispatcher flip;
        # per-route behavior now lives in ui_backend/routes/* and is pinned by the route
        # characterization tests).
        assert "def lambda_handler(" in content, "ui_backend handler should have lambda_handler function"
        assert "from routes import resolve_route" in content, "ui_backend handler should delegate to the routes package"

        # Check for required imports
        assert "from shared.auth" in content, "ui_backend handler should import from shared.auth"
        assert "from shared.utils" in content, "ui_backend handler should import from shared.utils"

if __name__ == "__main__":
    pytest.main([__file__])