"""
Error Handling Utilities
------------------------
Centralized error handling mechanisms for the application.

This module provides:
- Exception handling decorators for Celery tasks
- Standardized error formatting and logging
- Error notification utilities

CP-04: Implements structured logging and error handling
NX-06: Follows React Error Boundaries pattern for task errors
"""

import logging
import functools
import traceback
import json
from typing import Any, Callable, Dict, Optional, Type, Union
from celery import Task
from celery.exceptions import Retry

logger = logging.getLogger(__name__)

class CeleryTaskError(Exception):
    """Base exception for Celery task errors with additional context."""
    
    def __init__(self, message: str, original_exception: Optional[Exception] = None, 
                 task_id: Optional[str] = None, task_args: Optional[Any] = None):
        super().__init__(message)
        self.original_exception = original_exception
        self.task_id = task_id
        self.task_args = task_args
    
    def __str__(self) -> str:
        error_details = {
            'message': super().__str__(),
            'task_id': self.task_id,
            'original_error': str(self.original_exception) if self.original_exception else None
        }
        return json.dumps(error_details)


def task_error_handler(max_retries: int = 3, retry_backoff: bool = True,
                     retry_jitter: bool = True, retry_for: Optional[Union[Type[Exception], tuple]] = None):
    """
    Decorator for Celery tasks to standardize error handling and retries.
    
    Args:
        max_retries: Maximum retry attempts (default: 3)
        retry_backoff: Whether to use exponential backoff (default: True)
        retry_jitter: Whether to add randomness to retry delays (default: True)
        retry_for: Exception types that should trigger a retry 
                  (default: transient network errors)
    
    Returns:
        Decorated function with enhanced error handling
        
    Example:
        @shared_task(bind=True)
        @task_error_handler(max_retries=5)
        def my_task(self, *args, **kwargs):
            # Task code here
            pass
    """
    # Default retry exceptions if not specified
    if retry_for is None:
        import requests
        retry_for = (requests.exceptions.RequestException, TimeoutError, ConnectionError)
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(task_self: Task, *args, **kwargs) -> Any:
            task_id = getattr(task_self.request, 'id', 'unknown')
            
            try:
                # Run the actual task function
                return func(task_self, *args, **kwargs)
                
            except Retry:
                # Don't handle Retry exceptions - they're already being handled by Celery
                raise
                
            except retry_for as exc:
                # Handle retriable errors
                retry_count = task_self.request.retries
                
                logger.warning("Task %s encountered retriable error: %s. Retry %s/%s. Error type: %s",
                              task_id, str(exc), retry_count, max_retries, exc.__class__.__name__)
                
                # Determine retry delay with exponential backoff
                retry_delay = 2 ** retry_count if retry_backoff else 1
                
                # Try to retry the task
                if retry_count < max_retries:
                    raise task_self.retry(exc=exc, countdown=retry_delay, 
                                        max_retries=max_retries)
                else:
                    # Max retries exceeded
                    logger.error("Task %s failed after %s retries: %s",
                               task_id, max_retries, str(exc))
                    
                    # Return a standardized error result rather than raising an exception
                    return {
                        'status': 'failed',
                        'error': str(exc),
                        'error_type': exc.__class__.__name__,
                        'task_id': task_id,
                        'retries_exhausted': True
                    }
                    
            except Exception as exc:
                # Handle non-retriable errors
                logger.error("Task %s failed with unhandled exception: %s", 
                           task_id, str(exc))
                
                # Log traceback separately to avoid args conflict
                if exc.__traceback__:
                    logger.error("Traceback for task %s:\n%s", task_id, 
                               traceback.format_exc())
                
                # Return a standardized error result
                return {
                    'status': 'failed',
                    'error': str(exc),
                    'error_type': exc.__class__.__name__,
                    'task_id': task_id
                }
                
        return wrapper
    return decorator


def format_error_for_user(error: Exception) -> Dict[str, Any]:
    """
    Format an exception into a user-friendly error message.
    
    Args:
        error: The exception to format
        
    Returns:
        Dictionary with user-friendly error information
    """
    if isinstance(error, CeleryTaskError):
        # Special handling for our custom error type
        return {
            'message': str(error),
            'type': 'task_error',
            'task_id': error.task_id
        }
    
    # Map common error types to user-friendly messages
    error_type = error.__class__.__name__
    if 'Timeout' in error_type or 'ConnectionError' in error_type:
        message = "We're having trouble connecting to the server. Please try again later."
    elif 'ValidationError' in error_type:
        message = "There was a problem with the data provided. Please check and try again."
    elif 'AuthenticationError' in error_type or 'AuthorizationError' in error_type:
        message = "Authentication failed. Please refresh the page and try again."
    else:
        message = "An unexpected error occurred. Our team has been notified."
    
    return {
        'message': message,
        'type': error_type,
        'details': str(error) if not isinstance(error, Exception) else repr(error)
    }


def log_task_start(task_name: str, task_id: str, args: Any = None) -> None:
    """
    Log the start of a task with relevant context.
    
    Args:
        task_name: Name of the task
        task_id: ID of the task
        args: Arguments passed to the task (will be sanitized)
    """
    # Sanitize arguments to avoid logging sensitive data
    safe_args = str(args)
    if isinstance(args, dict) and ('password' in args or 'token' in args or 'key' in args):
        safe_args = {k: '***' if k in ('password', 'token', 'key', 'secret') else v 
                    for k, v in args.items()}
    
    logger.info("Starting task %s with ID %s", task_name, task_id)
    logger.debug("Task %s args: %s", task_id, safe_args)


def log_task_success(task_name: str, task_id: str, result: Any = None) -> None:
    """
    Log the successful completion of a task.
    
    Args:
        task_name: Name of the task
        task_id: ID of the task
        result: Result of the task (will be sanitized)
    """
    # Sanitize result to avoid logging sensitive data
    safe_result = "<result object>" if result else None
    
    logger.info("Task %s with ID %s completed successfully", task_name, task_id)
