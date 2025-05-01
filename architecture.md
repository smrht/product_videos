# Product Videos Architectuur

Dit document beschrijft de architectuur van het Product Videos project volgens de NX-01 regel (mappenstructuur) en CP-01 (Clean code) principes.

## Mappenstructuur

```mappstructuur
product_videos/
├── product_video_app/    # Django project hoofddirectory
│   ├── settings.py       # Django instellingen
│   ├── urls.py           # URL routes
│   ├── celery.py         # Celery configuratie
│   └── ...               # Overige Django bestanden
├── core/                 # Kern applicatiemodule
│   ├── models.py         # Database models (ProductPrompt, VideoGeneration)
│   ├── views.py          # Controllers/Views voor web interfaces
│   ├── forms.py          # Formulier definities
│   ├── tasks.py          # Celery taken
│   ├── tests/            # Testbestanden
│   ├── utils/            # Hulpprogramma's
│   │   └── error_handlers.py # Foutafhandeling voor Celery taken (CP-02, NX-05)
│   └── services/         # Business logica in services
│       └── prompt_service.py # Prompt generatie service
├── static/               # Statische bestanden (CSS, JS)
├── templates/            # HTML templates
└── docs/                 # Documentatie per feature
    └── celery_workflow.md # Documentatie voor Celery workflow
```

## Data Flow

```dataflow
[Client] → [Django View] → [S3/R2 Upload] → [Celery Tasks] → [AI APIs] → [S3/R2 Storage] → [Client]
```

### Gedetailleerde data flow

1. Gebruiker vult formulier in (afbeelding, titel, beschrijving)
2. Django view slaat afbeelding op in S3/R2
3. Django view triggert Celery orchestrator taak
4. Celery voert de asynchrone pipeline uit:
   - Prompt generatie (of hergebruik) met OpenRouter
   - Video generatie met behulp van de gegenereerde prompt
   - Opslag van resultaten en status-updates
5. UI toont voortgang en resultaat via HTMX polling
6. Uiteindelijke video wordt opgeslagen in S3/R2 en beschikbaar gemaakt voor de gebruiker

## Celery Pipeline Architectuur

### Taken en Dependencies

```celerypipeline
process_complete_video_generation
    └── generate_prompt_with_openrouter
        └── continue_video_callback (expliciet geregistreerde callback)
            └── generate_product_video
```

### State Management

- State tussen taken wordt doorgegeven via Redis backend
- Alleen task_id's worden doorgegeven als parameters tussen taken
- Data wordt opgehaald uit de backend om circulaire imports te voorkomen
- Elke taak heeft eigen verantwoordelijkheidsgebied (CP-01: Clean code)

### Error Handling (NX-05, CP-02)

- Gecentraliseerd via task_error_handler decorator
- Gestandaardiseerde foutresponsen in JSON-formaat
- Automatische retries voor netwerk-gerelateerde fouten
- Uitgebreide logging voor monitoring

## Authentication Flow

- Simpele email verificatie voor basis gebruikersidentificatie
- IP-gebaseerde limits voor gratis features
- Toekomstige uitbreiding: volledige gebruikersregistratie en authenticatie

## Performance Strategieën (CP-03)

- Asynchrone verwerking met non-blocking Celery chaining
- Prompt hergebruik om API calls te minimaliseren
- Resultaatcaching in Redis
- Efficiënte state overdracht tussen taken
