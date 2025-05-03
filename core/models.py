from django.db import models
import uuid
from django.utils import timezone

# Create your models here.

class ProductPrompt(models.Model):
    """
    Stores AI-generated prompts for product videos.
    
    This model keeps track of prompts generated for products,
    allowing for reuse and analysis of previous generations.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Product information
    product_title = models.CharField(max_length=255, help_text="Title of the product")
    product_description = models.TextField(help_text="Description of the product used for prompt generation")
    email = models.EmailField(help_text="Email of the user who requested the prompt")
    
    # Generated prompt data
    prompt_text = models.TextField(help_text="The generated prompt text")
    model_used = models.CharField(max_length=100, help_text="AI model used for generation")
    
    # Metadata and tracking
    created_at = models.DateTimeField(default=timezone.now)
    is_approved = models.BooleanField(default=True, help_text="Whether this prompt has been approved for use")
    
    # Optional category to help with optimization
    category = models.CharField(max_length=100, blank=True, null=True, 
                               help_text="Optional product category to help with prompt optimization")
    
    # Task reference
    task_id = models.CharField(max_length=255, blank=True, null=True, 
                              help_text="Reference to the Celery task that generated this prompt")
    
    class Meta:
        indexes = [
            models.Index(fields=['product_title']),
            models.Index(fields=['email']),
            models.Index(fields=['created_at']),
            models.Index(fields=['category']),
        ]
        verbose_name = "Product Prompt"
        verbose_name_plural = "Product Prompts"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Prompt for {self.product_title} ({self.id})"
    
    @classmethod
    def find_similar_prompt(cls, product_title, product_description, category=None):
        """
        Find a similar existing prompt for a product with similar title/description.
        
        Args:
            product_title: Title of the product to match
            product_description: Description of the product
            category: Optional product category
            
        Returns:
            ProductPrompt instance if found, None otherwise
        """
        # Simple implementation: just look for exact product title matches that are approved
        # In a production system, you might want to use more sophisticated text matching
        return cls.objects.filter(
            product_title__iexact=product_title,
            is_approved=True
        ).order_by('-created_at').first()


class VideoGeneration(models.Model):
    """
    Tracks video generation requests and their status.
    
    This model maintains a record of all video generation requests
    and links them to their associated prompts and output files.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # User information
    email = models.EmailField(help_text="Email of the user who requested the video")
    
    # Product information  
    product_title = models.CharField(max_length=255, help_text="Title of the product")
    product_description = models.TextField(help_text="Description of the product")
    
    # File references
    input_image_url = models.URLField(help_text="URL to the uploaded product image")
    output_video_url = models.URLField(blank=True, null=True, help_text="URL to the generated video (when complete)")
    
    # Prompt reference
    prompt = models.ForeignKey(
        ProductPrompt, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="video_generations",
        help_text="The prompt used for this video generation"
    )
    
    # Status tracking
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',
        help_text="Current status of the video generation"
    )
    
    # Metadata
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Task tracking
    task_id = models.CharField(max_length=255, blank=True, null=True, 
                              help_text="Reference to the Celery task")
    
    # Error information
    error_message = models.TextField(blank=True, null=True, 
                                   help_text="Error message if the generation failed")
    
    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = "Video Generation"
        verbose_name_plural = "Video Generations"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Video for {self.product_title} - {self.get_status_display()} ({self.id})"


class IPUsage(models.Model):
    """
    Tracks the number of free video generations per IP address.
    """
    ip_address = models.GenericIPAddressField(unique=True, db_index=True,
                                              help_text="The IP address of the user.")
    free_generations_used = models.PositiveIntegerField(default=0,
                                                       help_text="Number of free videos generated by this IP.")
    last_used_at = models.DateTimeField(auto_now=True,
                                       help_text="Timestamp of the last generation attempt by this IP.")

    class Meta:
        verbose_name = "IP Usage"
        verbose_name_plural = "IP Usages"
        ordering = ['-last_used_at']

    def __str__(self):
        return f"{self.ip_address} - {self.free_generations_used} free generations used"

    @classmethod
    def record_usage(cls, ip_address):
        """
        Records a free video generation attempt for an IP address.
        If the IP doesn't exist, it creates a new record with usage 1.
        If it exists, it increments the usage count.
        Returns the updated or new IPUsage instance.
        """
        usage, created = cls.objects.get_or_create(
            ip_address=ip_address,
            defaults={'free_generations_used': 1}
        )
        if not created:
            # Use F() expression for atomic update
            usage.free_generations_used = models.F('free_generations_used') + 1
            usage.save(update_fields=['free_generations_used', 'last_used_at'])
            usage.refresh_from_db() # Ensure we have the updated count
        return usage

    @classmethod
    def get_usage_count(cls, ip_address):
        """
        Gets the number of free generations used by an IP address.
        Returns 0 if the IP address is not found.
        """
        try:
            usage = cls.objects.get(ip_address=ip_address)
            return usage.free_generations_used
        except cls.DoesNotExist:
            return 0
