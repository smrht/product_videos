# core/services/image_editing_service.py

import logging
import os
from abc import ABC, abstractmethod
from django.conf import settings
from openai import OpenAI, APIError, RateLimitError
import requests
from io import BytesIO
from PIL import Image

logger = logging.getLogger(__name__)

# Abstracte klasse voor Image Editing Providers (CP-01: SOLID)
class BaseImageEditingProvider(ABC):
    """Abstract base class for image editing providers."""
    @abstractmethod
    def edit_image(self, image_url: str, prompt: str, **kwargs) -> str:
        """
        Edits an image based on a URL and a prompt.

        Args:
            image_url: The URL of the image to edit.
            prompt: The text prompt describing the desired edits.
            **kwargs: Additional provider-specific options.

        Returns:
            The URL of the edited image.

        Raises:
            NotImplementedError: If the provider does not support this method.
            Exception: For provider-specific errors (e.g., API errors, network issues).
        """
        raise NotImplementedError

# Concrete implementatie voor OpenAI (CP-01: Open/Closed Principle)
class OpenAIImageEditingProvider(BaseImageEditingProvider):
    """Image editing provider using OpenAI's DALL-E API."""

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY setting is not configured.")
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    def edit_image(self, image_url: str, prompt: str, size="1024x1024", **kwargs) -> str:
        """
        Edits an image using the OpenAI API (images.edit).

        Args:
            image_url: URL of the input image (must be PNG, square, < 4MB).
            prompt: Text prompt guiding the edit.
            size: The desired size of the output image (e.g., "1024x1024").
            **kwargs: Additional arguments for the OpenAI API call.

        Returns:
            The URL of the edited image.

        Raises:
            requests.exceptions.RequestException: If fetching the image fails.
            APIError: For general OpenAI API errors.
            RateLimitError: If the API rate limit is exceeded.
            ValueError: If the response format is unexpected.
        """
        logger.info(f"Starting OpenAI image edit for URL: {image_url} with prompt: '{prompt[:50]}...'" )

        # OpenAI image edit endpoint allows max 1000 characters for prompt
        if len(prompt) > 1000:
            logger.debug(f"Prompt length ({len(prompt)}) exceeds 1000 characters. Truncating.")
            prompt = prompt[:1000]
        logger.debug(f"Prompt length after truncation: {len(prompt)}")

        try:
            # 1. Download the image from the URL
            response = requests.get(image_url, stream=True, timeout=30) # Added timeout
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            image_bytes = response.content

            # Ensure image ≤ 4MB and square PNG as required by OpenAI
            img = Image.open(BytesIO(image_bytes)).convert("RGBA")
            # Make image square by padding transparent background
            max_side = max(img.size)
            square_img = Image.new("RGBA", (max_side, max_side), (0, 0, 0, 0))
            square_img.paste(img, ((max_side - img.width) // 2, (max_side - img.height) // 2))
            # Resize to 1024x1024 (OpenAI recommends 256/512/1024)
            square_img = square_img.resize((1024, 1024), Image.LANCZOS)

            png_buffer = BytesIO()
            square_img.save(png_buffer, format='PNG', optimize=True)
            png_buffer.seek(0)
            if png_buffer.getbuffer().nbytes > 4 * 1024 * 1024:
                logger.warning("PNG image larger than 4MB after processing; compressing further by reducing quality.")
                png_buffer_trunc = BytesIO()
                square_img.save(png_buffer_trunc, format='PNG', optimize=True, compress_level=9)
                png_buffer = png_buffer_trunc
                png_buffer.seek(0)

            # 2. Call the OpenAI API using (filename, fileobj) tuple to ensure correct MIME type
            api_response = self.client.images.edit(
                image=("image.png", png_buffer, "image/png"),  # filename, fileobj, MIME
                prompt=prompt,
                n=1,  # We only need one edited image
                size=size,
                **kwargs  # Pass any other relevant arguments
            )

            # 3. Extract the URL from the response
            if api_response.data and len(api_response.data) > 0 and api_response.data[0].url:
                edited_url = api_response.data[0].url
                logger.info(f"OpenAI image edit successful. Edited image URL: {edited_url}")
                return edited_url
            else:
                logger.error(f"OpenAI API response missing expected data structure. Response: {api_response}")
                raise ValueError("Invalid response format from OpenAI API")

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download image from URL {image_url}: {e}")
            raise # Re-raise the exception to be handled by the Celery task
        except RateLimitError as e:
            logger.error(f"OpenAI API rate limit exceeded: {e}")
            raise # Re-raise to allow Celery retries
        except APIError as e:
            logger.error(f"OpenAI API error during image edit: {e}")
            raise # Re-raise for Celery error handling
        except Exception as e:
            logger.error(f"An unexpected error occurred during OpenAI image editing: {e}")
            raise # Re-raise any other unexpected errors

# Factory pattern om de juiste provider te selecteren (CP-01: Design Patterns)
class ImageEditingService:
    """Service layer to manage different image editing providers."""
    def __init__(self):
        self.providers = {
            'openai': OpenAIImageEditingProvider(),
            # 'fal': FalAIImageEditingProvider(), # Toekomstige uitbreiding
        }

    def get_provider(self, provider_name: str = 'openai') -> BaseImageEditingProvider:
        provider = self.providers.get(provider_name)
        if not provider:
            logger.error(f"Unsupported image editing provider: {provider_name}")
            raise ValueError(f"Unsupported image editing provider: {provider_name}")
        return provider

    def edit_image(self, provider_name: str, image_url: str, prompt: str, **kwargs) -> str:
        """
        Edits an image using the specified provider.

        Args:
            provider_name: The name of the provider to use (e.g., 'openai').
            image_url: The URL of the image to edit.
            prompt: The text prompt describing the desired edits.
            **kwargs: Additional options for the provider.

        Returns:
            The URL of the edited image.

        Raises:
            ValueError: If the provider name is invalid.
            Exception: Provider-specific errors during editing.
        """
        provider = self.get_provider(provider_name)
        logger.info(f"Using {provider_name} for image editing.")
        try:
            return provider.edit_image(image_url, prompt, **kwargs)
        except Exception as e:
            logger.error(f"Image editing failed using {provider_name}: {e}")
            # Hier kan eventueel fallback logic of specifiekere error handling
            raise

# Singleton instance (optioneel, afhankelijk van hoe vaak je het gebruikt)
image_editing_service = ImageEditingService()
