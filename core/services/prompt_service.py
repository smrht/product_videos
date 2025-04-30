"""
Prompt Generation and Management Service
---------------------------------------
Handles the generation, storage, and retrieval of prompts for product videos.

This service coordinates:
- Finding existing similar prompts in the database
- Generating new prompts using OpenRouter
- Storing prompts in the database
- Optimizing prompts based on product category

CP-01: Follows SOLID principles with separation of concerns
CP-02: Implements input validation
CP-04: Includes comprehensive logging
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from django.db import transaction

from core.models import ProductPrompt, VideoGeneration
from .openrouter import create_client, OpenRouterError

logger = logging.getLogger(__name__)

class PromptService:
    """
    Service for managing product prompts, including generation and storage.
    
    This service serves as a façade for prompt-related operations, hiding the
    complexity of database queries and AI API interactions.
    """
    
    def __init__(self, openrouter_api_key: Optional[str] = None):
        """
        Initialize the prompt service.
        
        Args:
            openrouter_api_key: Optional API key override for OpenRouter
        """
        self.openrouter_client = create_client(openrouter_api_key)
    
    def get_or_generate_prompt(self, 
                              product_title: str, 
                              product_description: str,
                              email: str,
                              category: Optional[str] = None,
                              force_new: bool = False,
                              model: Optional[str] = None) -> Tuple[ProductPrompt, bool]:
        """
        Get an existing prompt or generate a new one.
        
        Args:
            product_title: Title of the product
            product_description: Description of the product
            email: User email for tracking
            category: Optional product category
            force_new: If True, always generate a new prompt
            model: Optional model override for OpenRouter
            
        Returns:
            Tuple of (ProductPrompt instance, bool indicating if it was newly created)
            
        Raises:
            OpenRouterError: If prompt generation fails
        """
        # Input validation (CP-02)
        if not product_title or not product_description or not email:
            raise ValueError("Product title, description, and email are required")
            
        # Try to find an existing prompt if not forcing new generation
        created = False
        if not force_new:
            existing_prompt = ProductPrompt.find_similar_prompt(
                product_title=product_title,
                product_description=product_description,
                category=category
            )
            
            if existing_prompt:
                logger.info(f"Found existing prompt for product '{product_title}'")
                return existing_prompt, created
                
        # No existing prompt found or forced new generation
        logger.info(f"Generating new prompt for product '{product_title}'")
        
        try:
            # Generate new prompt using OpenRouter
            result = self.openrouter_client.generate_prompt(
                product_title=product_title,
                product_description=product_description,
                model=model
            )
            
            # Create and save the new prompt
            with transaction.atomic():
                prompt = ProductPrompt(
                    product_title=product_title,
                    product_description=product_description,
                    email=email,
                    prompt_text=result['prompt'],
                    model_used=result['model_used'],
                    category=category
                )
                prompt.save()
                created = True
                
            logger.info(f"Created new prompt (id: {prompt.id}) for product '{product_title}'")
            return prompt, created
            
        except OpenRouterError as e:
            logger.error(f"Failed to generate prompt for '{product_title}': {str(e)}")
            raise
            
    def store_prompt_result(self, 
                          result: Dict[str, Any], 
                          email: str,
                          task_id: Optional[str] = None) -> ProductPrompt:
        """
        Store a prompt generation result from a Celery task.
        
        Args:
            result: The result dict from the Celery task
            email: User email
            task_id: Celery task ID
            
        Returns:
            Saved ProductPrompt instance
        """
        if result.get('status') != 'success':
            raise ValueError(f"Cannot store unsuccessful prompt result: {result.get('error', 'Unknown error')}")
            
        prompt = ProductPrompt(
            product_title=result['product_title'],
            product_description=result.get('product_description', ''),
            email=email,
            prompt_text=result['prompt'],
            model_used=result['model_used'],
            task_id=task_id
        )
        prompt.save()
        
        logger.info(f"Stored prompt result for task {task_id}")
        return prompt
        
    def get_prompts_for_user(self, email: str, limit: int = 10) -> List[ProductPrompt]:
        """
        Get recent prompts generated for a specific user.
        
        Args:
            email: User email
            limit: Maximum number of prompts to return
            
        Returns:
            List of ProductPrompt instances
        """
        return ProductPrompt.objects.filter(email=email).order_by('-created_at')[:limit]
    
    @staticmethod
    def optimize_prompt_for_category(prompt_text: str, category: str) -> str:
        """
        Optimize a prompt for a specific product category.
        
        Args:
            prompt_text: Original prompt text
            category: Product category
            
        Returns:
            Optimized prompt text
        """
        # Simple implementation for now - in a real system, this would 
        # have more sophisticated logic based on product categories
        
        category_enhancements = {
            'electronics': "Add focus on technical specifications and modern, clean lighting.",
            'clothing': "Emphasize fabric textures, draping, and natural movement.",
            'furniture': "Highlight craftsmanship, materials, and how it fits into a room setting.",
            'jewelry': "Use macro shots and dramatic lighting to capture sparkle and detail.",
            'food': "Showcase texture, color, and presentation with warm, appetizing lighting.",
        }
        
        enhancement = category_enhancements.get(category.lower(), "")
        if enhancement and enhancement not in prompt_text:
            return f"{prompt_text} {enhancement}"
        return prompt_text


# Convenience function for easier importing
def get_prompt_service(openrouter_api_key: Optional[str] = None) -> PromptService:
    """
    Create and return a configured prompt service.
    
    Args:
        openrouter_api_key: Optional API key override
        
    Returns:
        Configured PromptService instance
    """
    return PromptService(openrouter_api_key)
