# progress_tracking/serializers.py
# api/serializers.py
# progress_tracking/serializers.py
# api/serializers.py
from rest_framework import serializers
from progress_tracking.models import ProgressImage, Category, MaxUnit, MaxCategory, MaxData
from user.models import CustomUser
import base64
from django.core.files.base import ContentFile

class RegisterSerializer(serializers.ModelSerializer):
    password2 = serializers.CharField(write_only=True)  # ✅ Added this line
    profile_picture = serializers.ImageField(required=False)

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'first_name', 'last_name', 'country', 'password', 'password2', 'profile_picture']
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        password = validated_data.pop('password')
        password2 = validated_data.pop('password2')

        if password != password2:
            raise serializers.ValidationError("Passwords do not match")

        profile_picture = validated_data.pop('profile_picture', None)

        # Handle base64 image for web
        if isinstance(profile_picture, str):  # base64 string received
            decoded_image = base64.b64decode(profile_picture)
            profile_picture = ContentFile(decoded_image, name='profile_picture.jpg')

        user = CustomUser(**validated_data)
        user.set_password(password)
        if profile_picture:
            user.profile_picture = profile_picture
        user.save()
        return user


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name"]


class ProgressImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgressImage
        fields = ['id', 'image', 'category', 'date']
        read_only_fields = ['id', 'date', 'category']  # category now read-only

    def validate_image(self, value):
        if not value.content_type.startswith('image/'):
            raise serializers.ValidationError("Only image files are allowed.")
        return value

class MaxUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaxUnit
        fields = ["id", "name"]


class MaxCategorySerializer(serializers.ModelSerializer):
    unit = MaxUnitSerializer(read_only=True)
    unit_id = serializers.PrimaryKeyRelatedField(
        queryset=MaxUnit.objects.all(), source="unit", write_only=True
    )

    class Meta:
        model = MaxCategory
        fields = ["id", "name", "unit", "unit_id"]


class MaxDataSerializer(serializers.ModelSerializer):
    category = MaxCategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=MaxCategory.objects.all(), source="category", write_only=True
    )

    class Meta:
        model = MaxData
        fields = ["id", "user", "category", "category_id", "date", "value"]
        read_only_fields = ["user", "date"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)
