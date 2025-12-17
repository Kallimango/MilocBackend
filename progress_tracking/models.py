# progress_tracking/models.py
from django.db import models
from django.utils import timezone
from django.conf import settings
from user.models import CustomUser

class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class ProgressImage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress_images"
    )
    date = models.DateTimeField(default=timezone.now)
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="progress_images"
    )
    image = models.ImageField(upload_to="progress_images/", blank=False, null=False)
    is_public = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} - {self.category.name} - {self.date.strftime('%Y-%m-%d')}"



class ProgressVideo(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress_videos"
    )
    category = models.ForeignKey(
        'Category',  # Assuming you have a Category model
        on_delete=models.CASCADE,
        related_name="progress_videos"
    )
    video = models.FileField(upload_to="progress_videos/", blank=False, null=False)
    is_public = models.BooleanField(default=False)
    fps = models.FloatField(default=2.0)  # Store the frames per second
    start_date = models.DateTimeField(null=True, blank=True)  # Date of the first image
    end_date = models.DateTimeField(null=True, blank=True)  # Date of the last image
    created_at = models.DateTimeField(auto_now_add=True) 

    def __str__(self):
        # Safely handle None values for start_date and end_date
        start_date_str = self.start_date.strftime('%Y-%m-%d') if self.start_date else 'Unknown Start Date'
        end_date_str = self.end_date.strftime('%Y-%m-%d') if self.end_date else 'Unknown End Date'

        return f"{self.user.username} - {self.category.name} - {start_date_str} to {end_date_str}"

class MaxUnit(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

class MaxCategory(models.Model):
    name = models.CharField(max_length=50, unique=True)
    unit = models.ForeignKey(
        "MaxUnit",
        on_delete=models.CASCADE,
        related_name="max_category"
    )

    def __str__(self):
        return self.name

class MaxData(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="max_data_entries"  # changed from 'progress_videos'
    )
    category = models.ForeignKey(
        "MaxCategory",
        on_delete=models.CASCADE,
        related_name="max_data"
    )
    date = models.DateTimeField(default=timezone.now)
    value = models.IntegerField(blank=True)  # removed max_length

    def __str__(self):
        return f"{self.user.username} - {self.category.name} - {self.date.strftime('%Y-%m-%d')}"
