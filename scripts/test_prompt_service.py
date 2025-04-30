#!/usr/bin/env python
"""
Prompt Service Test Script
-------------------------
This script tests the full prompt generation service including database interactions.

Usage:
    python test_prompt_service.py [--env ENV_FILE_PATH] [--force-new]

Default behavior:
- Loads environment variables from ../.env
- Creates a PromptService instance
- Gets or generates a prompt for a test product
- Displays the result and whether it was retrieved or newly generated

CP-02: Includes validation of environment
CP-04: Implements logging for testing purposes
"""

import os
import sys
import argparse
import logging
import dotenv
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configure path to find the app
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.append(str(project_root))

# Django setup is needed to use the models and services
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'product_video_app.settings')
import django
django.setup()

# Now we can import our services and models
from core.services.prompt_service import get_prompt_service
from core.models import ProductPrompt


def main():
    """Run the prompt service test."""
    parser = argparse.ArgumentParser(description="Test the Prompt Service")
    parser.add_argument('--env', type=str, help="Path to .env file", default=str(project_root / '.env'))
    parser.add_argument('--force-new', action='store_true', help="Force new prompt generation")
    args = parser.parse_args()

    # Load environment variables
    if os.path.exists(args.env):
        logger.info(f"Loading environment from {args.env}")
        dotenv.load_dotenv(args.env)
    else:
        logger.warning(f"Environment file {args.env} not found!")
        logger.warning("Make sure OPENROUTER_API_KEY is set in your environment.")

    # Check for API key
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        logger.error("Error: OPENROUTER_API_KEY not found in environment")
        logger.error("Please set this variable in your .env file or environment")
        sys.exit(1)

    # Test product data
    test_products = [
        {
            'product_title': 'Premium Wireless Headphones',
            'product_description': 'Studio-quality sound with active noise cancellation, '
                                'premium leather ear cups, and 30-hour battery life. '
                                'Includes voice assistant integration and touch controls.',
            'email': 'test@example.com',
            'category': 'electronics'
        },
        {
            'product_title': 'Ergonomic Office Chair',
            'product_description': 'Premium office chair with lumbar support, adjustable height, '
                                'and breathable mesh back. Perfect for long work sessions with '
                                'comfortable armrests and smooth-rolling casters.',
            'email': 'test@example.com',
            'category': 'furniture'
        }
    ]

    try:
        # Create prompt service
        logger.info("Creating Prompt Service...")
        prompt_service = get_prompt_service()
        
        # Process each test product
        for i, product in enumerate(test_products):
            logger.info(f"\nTesting with product {i+1}: {product['product_title']}")
            
            # Get or generate a prompt
            prompt, created = prompt_service.get_or_generate_prompt(
                product_title=product['product_title'],
                product_description=product['product_description'],
                email=product['email'],
                category=product['category'],
                force_new=args.force_new
            )
            
            # Display results
            print("\n" + "="*80)
            if created:
                print(f"✅ Generated NEW prompt for: {product['product_title']}")
            else:
                print(f"📋 Retrieved EXISTING prompt for: {product['product_title']}")
            print("="*80)
            print(f"Prompt ID: {prompt.id}")
            print(f"Model used: {prompt.model_used}")
            print(f"Created at: {prompt.created_at}")
            print("-"*80)
            print(prompt.prompt_text)
            print("="*80)
            
        # Display prompt count in the database
        prompt_count = ProductPrompt.objects.count()
        print(f"\nTotal prompts in database: {prompt_count}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
