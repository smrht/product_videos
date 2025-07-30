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
                                break  # Exit loop on completion
                            elif status_value in ['FAILED', 'ERROR']:
                                error_detail = status_data.get('logs', 'No specific error detail provided.')
                                logger.error(f"Fal AI job failed with status {status_value}. Detail: {error_detail}")
                                raise FalServiceError(f"Fal AI job failed: {status_value}. Detail: {error_detail}")
                            # Other statuses like IN_PROGRESS, IN_QUEUE: continue polling
                            
                        else:
                            logger.warning(f"'status' key not found in status response data: {status_data}")
                            # Consider how to handle this - maybe retry or fail?
                            
                    except requests.exceptions.JSONDecodeError:
                        logger.error(f"Failed to decode JSON from status response. Status code: {status_resp.status_code}, Response text: {status_resp.text}")
                        # Optionally, handle specific status codes like 5xx differently
                        
                    # Always raise for bad status codes *after* trying to log useful info
                    status_resp.raise_for_status() 
                    
                except requests.exceptions.RequestException as e:
                    logger.error(f"Error polling Fal AI status on attempt {attempt+1}: {e}")
                    # Consider retry logic or failure after several attempts
                    if attempt == max_polls - 1:
                        raise FalServiceError(f"Failed to get Fal AI status after {max_polls} attempts: {e}") from e
                
                logger.info(f"Waiting {poll_interval}s before next status check ({attempt + 1}/{max_polls})")
                time.sleep(poll_interval)
                
            if not completion_status:
                raise FalServiceError(f"Fal AI job did not complete after {max_polls} attempts.")
                
            # 2. Fetch the final result if completed
            result_url = status_data.get('response_url')
            if not result_url:
                raise FalServiceError("No response_url found in completed status data.")
                
            # Add a small delay before fetching the result
            logger.info("Completion detected. Waiting 2 seconds before fetching result...")
            time.sleep(2)
            
            logger.info(f"Fetching final result from {result_url}")
            try:
                result_resp = requests.get(result_url, headers=headers, timeout=60) # Longer timeout for result download
                result_resp.raise_for_status()  # Raise exception for 4xx/5xx
                
                result_data = result_resp.json()
                logger.debug(f"Fal AI result data: {result_data}")
                
                if 'video' not in result_data or 'url' not in result_data['video']:
                    logger.error(f"Unexpected result structure from Fal AI: {result_data}")
                    raise FalServiceError("Unexpected result structure from Fal AI: 'video.url' not found.")
                    
                video_url = result_data['video']['url']
                logger.info(f"Successfully retrieved video URL: {video_url}")
                return video_url
                
            except requests.exceptions.HTTPError as e:
                # Log the detailed error response before raising
                error_content = result_resp.text  # Get the raw response body
                logger.error(f"HTTP error fetching Fal AI result: {e}. Response status: {result_resp.status_code}. Response body: {error_content}")
                raise FalServiceError(f"Failed to fetch Fal AI video result: {e}. Details: {error_content}") from e
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error fetching Fal AI result: {e}")
                raise FalServiceError(f"Failed to fetch Fal AI video result: {str(e)}") from e
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Fal AI API request failed: {e}")
            raise FalServiceError(f"Fal AI API request failed: {str(e)}") from e
        except Exception as e:
            logger.exception("An unexpected error occurred during Fal AI video generation.") # Log full traceback
            raise FalServiceError(f"Unexpected error during Fal AI video generation: {str(e)}")

# Instantiate the service for easy import
fal_service = FalService()
