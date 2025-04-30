#!/usr/bin/env python
"""
OpenRouter API Test Script
-------------------------
This script tests the OpenRouter API integration by generating a prompt for a sample product.
It's useful for development and debugging without running the full Django application.

Usage:
    python test_openrouter.py [--env ENV_FILE_PATH]

Default behavior:
- Loads environment variables from ../.env
- Creates an OpenRouter client
- Generates a prompt for a test product
- Prints the result

NX-06: Implements standard error handling for API testing
CP-02: Validates environment and provides secure defaults
"""

import os
import sys
import argparse
import dotenv
from pathlib import Path

# Configure path to find the app
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.append(str(project_root))

# Django setup is needed to use the OpenRouter client
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'product_video_app.settings')
import django
django.setup()

# Now we can import our OpenRouter client
from core.services.openrouter import create_client, OpenRouterError


def main():
    """Run the OpenRouter API test."""
    parser = argparse.ArgumentParser(description="Test the OpenRouter API integration")
    parser.add_argument('--env', type=str, help="Path to .env file", default=str(project_root / '.env'))
    args = parser.parse_args()

    # Load environment variables
    if os.path.exists(args.env):
        print(f"Loading environment from {args.env}")
        dotenv.load_dotenv(args.env)
    else:
        print(f"Warning: Environment file {args.env} not found!")
        print("Make sure OPENROUTER_API_KEY is set in your environment.")

    # Check for API key
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        print("Error: OPENROUTER_API_KEY not found in environment")
        print("Please set this variable in your .env file or environment")
        sys.exit(1)

    # Test product data
    test_product = {
        'product_title': 'Ergonomic Office Chair',
        'product_description': 'Premium office chair with lumbar support, adjustable height, '
                             'and breathable mesh back. Perfect for long work sessions with '
                             'comfortable armrests and smooth-rolling casters.'
    }

    try:
        # Create client and generate prompt
        print(f"Creating OpenRouter client...")
        client = create_client()
        
        print(f"Generating prompt for test product: {test_product['product_title']}")
        result = client.generate_prompt(
            product_title=test_product['product_title'], 
            product_description=test_product['product_description']
        )
        
        # Display results
        print("\n" + "="*80)
        print(f"✅ Success! Generated prompt:")
        print("="*80)
        print(f"Model used: {result['model_used']}")
        print("-"*80)
        print(result['prompt'])
        print("="*80)
        
    except OpenRouterError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
