# Celery Video Generatie Workflow

Deze documentatie beschrijft de volledige video generatie workflow geïmplementeerd met Celery in het Product Videos project.

## Overzicht

De workflow bestaat uit een reeks asynchrone taken die een product afbeelding omzetten in een video met behulp van AI. De volgende stappen worden uitgevoerd:

1. **Upload verwerken**: Afbeelding en metadata worden geüpload naar S3/R2
2. **Prompt genereren**: Unieke of hergebruikte AI prompt voor de video generatie
3. **Video genereren**: AI-aangestuurde video creatie op basis van de afbeelding en prompt
4. **Status bijwerken**: Gebruiker ontvangt statusupdates via UI polling

## Implementatie details (CP-01, CP-03)

### Orchestrator Taak

De hoofdtaak die het gehele proces orchestreert:

```python
@shared_task(bind=True)
@task_error_handler(max_retries=2)
def process_complete_video_generation(self, data):
    # Creëert een VideoGeneration record en slaat originele gegevens op in Redis
    # Start de prompt generatie en koppelt callback voor volgende stappen
    # Non-blocking uitvoering zonder .get() calls
```

### Prompt Generatie

```python
@shared_task(bind=True)
@task_error_handler(max_retries=3)
def generate_prompt_with_openrouter(self, product_data):
    # Controleert op bestaande prompts voor hergebruik
    # Indien nodig, nieuwe prompt genereren via OpenRouter API
    # Retourneert prompt en metadata
```

### Callback Mechanisme

Expliciet geregistreerde callback taak:

```python
@shared_task(name="core.tasks.continue_video_callback")
def _continue_with_video_generation_callback(prompt_data, parent_task_id):
    # Wrapper om de raw implementatie aan te roepen
    return _continue_with_video_generation_raw(prompt_data, parent_task_id)
```

De hoofdfunctionaliteit:

```python
def _continue_with_video_generation_raw(prompt_data, parent_task_id):
    # Haalt originele data uit Redis
    # Verrijkt data met prompt resultaten
    # Start de video generatie taak
```

### Video Generatie

```python
@shared_task(bind=True)
@task_error_handler(max_retries=3)
def generate_product_video(self, data=None):
    # Valideert input data
    # Gebruikt de prompt om een video te genereren (mock in huidige versie)
    # Slaat het resultaat op en werkt database bij
```

## Usage voorbeeld

Aanroepen vanuit Django view:

```python
from core.tasks import process_complete_video_generation

# Formulier data verzamelen
task_data = {
    'email': form.cleaned_data['email'],
    'product_title': form.cleaned_data['product_title'],
    'product_description': form.cleaned_data['product_description'],
    'file_url': file_url
}

# Task starten
task = process_complete_video_generation.delay(task_data)

# Task ID opslaan voor status polling
request.session['last_task_id'] = task.id
```

## State Management (CP-01, CP-03)

Data wordt tussen taken gedeeld via Redis:

```python
# Opslaan van data in Redis
state_key = f"video_gen_data_{task_id}"
app.backend.set(state_key, json.dumps(data))

# Ophalen van data in callback
original_data_json = app.backend.get(state_key)
original_data = json.loads(original_data_json)
```

## Error Handling (NX-05, CP-02)

Alle taken gebruiken de `task_error_handler` decorator voor consistente foutafhandeling:

```python
@task_error_handler(max_retries=3)
def example_task(self, data):
    # De decorator vangt alle fouten op en:
    # - Geeft gestandaardiseerde foutresponsen
    # - Voert automatische retries uit voor netwerk fouten
    # - Logt details voor debugging
```

## Uitbreidingen

De huidige workflow kan worden uitgebreid met:

1. Echte OpenAI API calls voor afbeeldingsbewerking
2. Fal AI integratie voor video generatie
3. Email notificaties bij voltooiing
4. Betaalde features (premium modellen, extra parameters)

## Monitoring

Voortgang volgen:

1. Task status endpoints beschikbaar via `/task-status/<task_id>/`
2. Celery worker logs voor gedetailleerde debugging
3. Database records in `VideoGeneration` model
