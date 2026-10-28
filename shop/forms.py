from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Product, SellerProfile


class MultiImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.ImageField):
    widget = MultiImageInput

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            files = []
            for item in data:
                if item in (None, ''):
                    continue
                files.append(super().clean(item, initial))
            return files
        return super().clean(data, initial)


class ProductForm(forms.ModelForm):
    images = MultipleFileField(
        required=False,
        widget=MultiImageInput(attrs={'multiple': True, 'accept': 'image/*'}),
        help_text='Upload up to 8 product photos.'
    )

    class Meta:
        model = Product
        fields = ['name', 'slug', 'category', 'price', 'stock', 'image', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Product name'}),
            'slug': forms.TextInput(attrs={'placeholder': 'product-slug'}),
            'description': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Describe your product...'}),
            'price': forms.NumberInput(attrs={'min': '1', 'step': '0.01'}),
            'stock': forms.NumberInput(attrs={'min': '0'}),
        }

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is not None and price <= 0:
            raise ValidationError('Price must be greater than zero.')
        return price

    def clean_images(self):
        uploaded_files = self.cleaned_data.get('images', [])
        if not isinstance(uploaded_files, (list, tuple)):
            uploaded_files = [uploaded_files] if uploaded_files else []
        if len(uploaded_files) > 8:
            raise ValidationError('You can upload up to 8 images per product.')
        if any(uploaded_file.size > 5 * 1024 * 1024 for uploaded_file in uploaded_files):
            raise ValidationError('Each product image must be 5 MB or smaller.')
        return uploaded_files


class SellerProfileForm(forms.ModelForm):
    class Meta:
        model = SellerProfile
        fields = ['business_name', 'phone', 'city', 'bio', 'avatar']
        widgets = {
            'business_name': forms.TextInput(attrs={'placeholder': 'Your shop name'}),
            'phone': forms.TextInput(attrs={'placeholder': '+98 ...'}),
            'city': forms.TextInput(attrs={'placeholder': 'Tehran'}),
            'bio': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Tell buyers about your shop...'}),
        }


class CheckoutForm(forms.Form):
    customer_name = forms.CharField(max_length=120, label='Full name')
    customer_email = forms.EmailField(required=False, label='Email')
    customer_phone = forms.CharField(max_length=20, required=False, label='Phone')
    address = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 4}), label='Delivery address')


class AccountProfileForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ['first_name', 'last_name']
        widgets = {
            'first_name': forms.TextInput(attrs={'autocomplete': 'given-name'}),
            'last_name': forms.TextInput(attrs={'autocomplete': 'family-name'}),
        }


class SignupEmailForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'placeholder': 'you@example.com', 'autocomplete': 'email'}),
        label='Email address',
    )


class SignupCodeForm(forms.Form):
    code = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={'placeholder': '123456', 'inputmode': 'numeric', 'autocomplete': 'one-time-code'}),
        label='Verification code',
    )


class SignupCompleteForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'Your username', 'autocomplete': 'username'}),
        label='Username',
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Create a strong password', 'autocomplete': 'new-password'}),
        label='Password',
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Repeat your password', 'autocomplete': 'new-password'}),
        label='Confirm password',
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if not username:
            raise forms.ValidationError('Username is required.')
        user_model = get_user_model()
        if user_model.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Passwords do not match.')
        return cleaned_data
