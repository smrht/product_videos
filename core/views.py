from django.shortcuts import render
from .forms import ProductVideoForm

# Create your views here.
def index_view(request):
    form = ProductVideoForm()
    context = {'form': form}
    return render(request, 'core/index.html', context)
