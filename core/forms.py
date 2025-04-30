from django import forms

class ProductVideoForm(forms.Form):
    product_photo = forms.FileField(
        label='Product Photo',
        required=True,
        help_text='Upload a clear image of your product.' # Basic help text, enhance with tooltips in template
        # TODO: Add file size/type validation if needed
    )
    product_title = forms.CharField(
        label='Product Title',
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'e.g., Premium Wireless Speaker'}),
        help_text='Enter a concise and descriptive title.'
    )
    product_description = forms.CharField(
        label='Product Description',
        required=True,
        widget=forms.Textarea(attrs={'placeholder': 'Describe the key features and benefits...'}),
        help_text='Provide details about the product.'
    )
    email = forms.EmailField(
        label='Email Address',
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'your.email@example.com'}),
        help_text='We will send the generated video link to this email.'
    )
