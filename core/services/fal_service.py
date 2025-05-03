import os
import json
import logging
import requests
from django.conf import settings
from urllib.parse import urljoin
import time

logger = logging.getLogger(__name__)

class FalServiceError(Exception):
    """Custom exception for FalService errors."""
    pass

class FalService:
    """Service to interact with the Fal AI API for video generation using direct REST API calls."""

    def __init__(self):
        self.api_key = getattr(settings, 'FAL_API_KEY', os.environ.get('FAL_API_KEY'))
        if not self.api_key:
            logger.error("FAL_API_KEY is not configured in settings or environment.")
            # Don't raise here, to allow for simulation mode or default videos in generate_svd_video
        self.base_url = "https://queue.fal.run/"
        self.queue_endpoint = "fal-ai/kling-video/v1.6/standard/image-to-video"
        self.timeout = 120  # 2 minutes timeout for requests

    def generate_svd_video(self, image_url: str, duration: str = '5') -> str:
        """
        Generates a video using Fal AI's Kling Video model via REST API.
        
        Args:
            image_url: The URL of the input image (can be relative path from S3).
        
        Returns:
            The URL of the generated video.
        
        Raises:
            FalServiceError: If the API call fails or returns an error.
        """
        logger.info(f"Starting Fal AI Kling video generation for image: {image_url}")
        
        # If no API key, return a mock video URL for testing/development
        if not self.api_key:
            logger.warning("No FAL_API_KEY found. Returning mock video URL.")
            return f"https://mock-fal-ai.com/video/{image_url.replace('/', '_')}.mp4"
        
        # Ensure the image_url has a proper scheme (http/https)
        # If it's a relative path, convert it to a full URL
        if image_url.startswith('/'):
            # Convert relative URL to absolute URL using settings
            base_url = getattr(settings, 'MEDIA_URL_EXTERNAL', 'https://example.com')
            if base_url.endswith('/') and image_url.startswith('/'):
                image_url = base_url + image_url[1:]
            else:
                image_url = base_url + image_url
            logger.info(f"Converted relative URL to absolute URL: {image_url}")
            
        # Prepare the request payload
        payload = {
            "image_url": image_url,
            "prompt": "Product rotating on a clean white background, high-quality professional turntable video",
            "duration": duration,  # Use the duration parameter passed from the form
            "aspect_ratio": "1:1",  # Square videos work better with product images
            "negative_prompt": "blur, distort, and low quality",
            "cfg_scale": 0.5
        }
        
        headers = {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            # Step 1: Submit the request to start processing
            request_url = urljoin(self.base_url, self.queue_endpoint)
            logger.info(f"Submitting Fal AI request to {request_url} with payload: {payload}")
            
            response = requests.post(
                request_url,
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            submit_result = response.json()
            logger.debug(f"Fal AI API submission result: {submit_result}")
            
            if 'request_id' not in submit_result:
                raise FalServiceError("No request_id in Fal AI response")
                
            request_id = submit_result['request_id']
            logger.info(f"Fal AI request submitted successfully with request_id: {request_id}")
            
            # Wait longer for initial processing - video generation takes time
            time.sleep(10)
            
            # ===================================
            # TWO-STEP POLLING: STATUS THEN RESULT
            # ===================================
            
            # First verify that the image URL is publicly accessible
            try:
                img_check = requests.head(image_url, timeout=10)
                if img_check.status_code != 200:
                    logger.error(f"Image URL is not accessible (status {img_check.status_code}): {image_url}")
                    raise FalServiceError(f"Image URL is not publicly accessible: {image_url}")
                logger.info(f"Image URL is accessible: {image_url}")
            except requests.RequestException as e:
                logger.error(f"Failed to verify image accessibility: {e}")
                # Continue anyway as this is just a check
                
            # 1. First poll for status until completed
            # Following exact Fal.ai API documentation
            status_endpoint = f"fal-ai/kling-video/requests/{request_id}/status"  
            status_url = urljoin(self.base_url, status_endpoint)
            
            # Poll for status longer since video generation takes time
            max_polls = 45  # Poll for up to ~9 minutes (video gen can take time)
            poll_interval = 12  # seconds between polls
            status_data = None
            completion_status = False
            
            logger.info(f"Polling for status at {status_url}")
            
            # Poll for status until completed
            for attempt in range(max_polls):
                try:
                    status_resp = requests.get(status_url, headers=headers, timeout=30)
                    # Log the response regardless of status code
                    logger.info(f"Status poll attempt {attempt+1} response: {status_resp.status_code}")
                    
                    # Even if we get 4xx, try to parse the response
                    try:
                        status_data = status_resp.json()
                        logger.info(f"Status data: {status_data}")
                        
                        if 'status' in status_data:
                            status_value = status_data['status']
                            logger.info(f"Status poll attempt {attempt+1}: Status = {status_value}")
                            
                            if status_value == 'COMPLETED':
                                logger.info(f"Request completed on attempt {attempt+1}")
                                completion_status = True
                                break
                            elif status_value in ('FAILED', 'CANCELLED', 'ERROR'):
                                error_msg = status_data.get('message', 'Unknown error')
                                raise FalServiceError(f"Fal AI request failed: {error_msg}")
                    except (ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"Status poll attempt {attempt+1} returned invalid JSON: {e}")
                        # Just log and continue polling
                except requests.RequestException as e:
                    logger.warning(f"Status poll attempt {attempt+1} failed with request error: {e}")
                    # Don't stop polling on request error
                
                # Wait before trying again
                logger.info(f"Waiting {poll_interval}s before next status check ({attempt+1}/{max_polls})")
                time.sleep(poll_interval)
            
            # 2. Now get the result - even if status polling had issues
            # This is the direct result URL per documentation
            result_endpoint = f"fal-ai/kling-video/requests/{request_id}"
            result_url = urljoin(self.base_url, result_endpoint)
            
            logger.info(f"Fetching final result from {result_url}")
            
            try:
                result_resp = requests.get(result_url, headers=headers, timeout=30)
                result_resp.raise_for_status()  # Raise exception for 4xx/5xx
                result = result_resp.json()
                logger.debug(f"Fal AI API result: {result}")
                
                # Check if video URL exists in the response
                if 'video' in result and 'url' in result['video']:
                    logger.info(f"Found video URL in API response")
                else:
                    # If we don't find a video URL but made it this far, something is odd
                    logger.warning(f"Video URL not found in result despite successful request: {result}")
                    if settings.DEBUG:
                        logger.warning("DEBUG=True, returning mock video URL")
                        return f"https://storage.googleapis.com/falserverless/sample_videos/turntable_demo.mp4"
                    raise FalServiceError(f"Video URL not found in Fal AI response: {result}")
            except requests.RequestException as e:
                logger.error(f"Failed to fetch final result: {e}")
                if settings.DEBUG:
                    logger.warning("DEBUG=True, returning mock video URL after failure")
                    return f"https://storage.googleapis.com/falserverless/sample_videos/turntable_demo.mp4" 
                raise FalServiceError(f"Failed to fetch Fal AI video result: {str(e)}")
            
            # Process the result - extract the video URL
            # Kling response might be { "video": {"url": ... } }
            if isinstance(result, dict):
                # Look for typical keys
                if 'video' in result and isinstance(result['video'], dict) and 'url' in result['video']:
                    video_url = result['video']['url']
                elif 'videos' in result and isinstance(result['videos'], list) and len(result['videos'])>0 and 'url' in result['videos'][0]:
                    video_url = result['videos'][0]['url']
                else:
                    raise FalServiceError(f"Video URL not found in Fal AI response: {result}")
                logger.info(f"Fal AI video generated successfully: {video_url}")
                return video_url
            raise FalServiceError(f"Unexpected response structure from Fal AI: {result}")
                
        except requests.RequestException as e:
            logger.error(f"Fal API HTTP request error: {e}", exc_info=True)
            raise FalServiceError(f"Fal AI API request failed: {str(e)}") from e
        except json.JSONDecodeError as e:
            logger.error(f"Fal API JSON decode error: {e}", exc_info=True)
            raise FalServiceError(f"Invalid JSON response from Fal AI: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error during Fal AI video generation: {e}", exc_info=True)
            raise FalServiceError(f"Fal AI video generation failed: {str(e)}") from e

# Instantiate the service for easy import
fal_service = FalService()
