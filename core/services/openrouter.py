"""
OpenRouter API Client Module
----------------------------
Handles interactions with the OpenRouter API for AI prompt generation and optimization.
This module provides:
- Configuration management for OpenRouter API
- Methods for prompt generation from product info
- Error handling and retry mechanisms
- Response processing and validation

Dependencies:
- requests==2.32.3: HTTP library for API calls
"""

import os
import time
import json
import logging
import requests
from typing import Dict, Any, Optional, Union, List

logger = logging.getLogger(__name__)

class OpenRouterError(Exception):
    """Base exception for OpenRouter API errors."""
    pass

class OpenRouterClient:
    """
    Client for interacting with the OpenRouter API.
    
    Handles authentication, request formation, and response parsing
    for prompt generation through OpenRouter's API.
    """
    
    BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "openai/gpt-4.1"  # Default high-capability model for detailed prompts
    RETRY_ATTEMPTS = 3
    RETRY_DELAY = 2  # seconds
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the OpenRouter client.
        
        Args:
            api_key: OpenRouter API key. If None, will attempt to read from OPENROUTER_API_KEY env var.
        
        Raises:
            OpenRouterError: If API key is not provided and not found in environment variables.
        """
        self.api_key = api_key or os.environ.get('OPENROUTER_API_KEY')
        if not self.api_key:
            raise OpenRouterError("OpenRouter API key is required. Set OPENROUTER_API_KEY environment variable.")
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("SITE_URL", "https://example.com"),  # Replace with actual site URL
            "X-Title": "Product Video Generator"  # App name for tracking in OpenRouter
        }
    
    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """
        Process API response and handle errors.
        
        Args:
            response: Response object from requests call
            
        Returns:
            Parsed JSON response
            
        Raises:
            OpenRouterError: For API errors with appropriate message
        """
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_detail = response.text
            try:
                error_detail = response.json()
            except:
                pass
            
            logger.error(f"OpenRouter API error: {e}, Details: {error_detail}")
            raise OpenRouterError(f"OpenRouter API error: {e}. Details: {error_detail}")
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            logger.error(f"Request error: {e}")
            raise OpenRouterError(f"Request error: {e}")
    
    def generate_prompt(self, 
                      product_title: str, 
                      product_description: str,
                      model: str = None,
                      max_retries: int = None) -> Dict[str, Any]:
        """
        Generate optimized prompt for product video creation.
        
        Args:
            product_title: Title of the product
            product_description: Description of the product
            model: OpenRouter model to use (default: self.DEFAULT_MODEL)
            max_retries: Maximum retry attempts (default: self.RETRY_ATTEMPTS)
            
        Returns:
            Dict containing prompt details and API response
            
        Raises:
            OpenRouterError: If API call fails after retries
        """
        model = model or self.DEFAULT_MODEL
        max_retries = max_retries or self.RETRY_ATTEMPTS
        retries = 0
        
        system_prompt = """
        You are a professional product marketing expert specializing in video script creation.
        Your task is to create a detailed, creative prompt for generating a 3D turntable product video.
        Consider the product's features, benefits, and visual aspects.
        Focus on creating a prompt that will highlight the product's best visual elements.
        The result should be a paragraph that describes how to showcase the product in a 3D rotating view.
        """
        
        user_message = f"""
        Product: {product_title}
        
        Description: {product_description}
        
        Please create a detailed prompt for a 3D turntable video of this product.
        Include specific details about:
        1. The product's appearance and key visual features
        2. The environment/background that would best showcase it
        3. Lighting suggestions
        4. Camera angles and movements for the turntable effect
        5. Any special effects that would enhance the presentation
        
        Create a cohesive, detailed paragraph that can be used as a prompt for AI video generation.
        """
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        }
        
        while retries < max_retries:
            try:
                url = f"{self.BASE_URL}/chat/completions"
                response = requests.post(url, json=payload, headers=self.headers)
                result = self._handle_response(response)
                
                # Extract the generated prompt from the response
                if result.get('choices') and len(result['choices']) > 0:
                    prompt_content = result['choices'][0]['message']['content'].strip()
                    
                    return {
                        'prompt': prompt_content,
                        'model_used': model,
                        'product_title': product_title,
                        'raw_response': result
                    }
                else:
                    raise OpenRouterError("No content found in response")
                    
            except OpenRouterError as e:
                retries += 1
                if retries >= max_retries:
                    logger.error(f"Failed after {max_retries} attempts: {e}")
                    raise
                
                logger.warning(f"Retry {retries}/{max_retries} after error: {e}")
                time.sleep(self.RETRY_DELAY * retries)  # Increasing backoff
        
        # This should not be reached due to the raise in the loop
        raise OpenRouterError("Unexpected error in retry loop")


# Convenience function for easier importing
def create_client(api_key: Optional[str] = None) -> OpenRouterClient:
    """
    Create and return a configured OpenRouter client.
    
    Args:
        api_key: Optional API key override
        
    Returns:
        Configured OpenRouterClient instance
    """
    return OpenRouterClient(api_key)
