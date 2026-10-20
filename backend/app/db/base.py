"""
Database Model Base Configuration

This module collects all SQLModel metadata for Alembic autogenerate.

What is this for?
This file imports all database models so that Alembic (the database migration tool)
can discover them and automatically generate migration scripts.

How it works:
1. When you run `alembic revision --autogenerate`, Alembic imports this file
2. This file imports all your models, making them available to Alembic
3. Alembic compares the current database state with your model definitions
4. It generates a migration script with the differences

Why import all models here?
- Alembic needs to know about all models to detect schema changes
- Centralizing imports makes it easy to add new models
- The `# noqa: F401` comments tell linters these imports are intentional

How to use:
    # When you create a new model, add it here:
    from app.features.your_feature.models import YourNewModel  # noqa: F401
    
    # Then run:
    # alembic revision --autogenerate -m "Add YourNewModel"
    # alembic upgrade head
"""

# Collects all SQLModel metadata for Alembic autogenerate.
from app.features.api_keys.models import UserApiKey  # noqa: F401
from app.features.auth.models import PasswordResetToken  # noqa: F401
from app.features.chat.models import Conversation, ProjectAccess  # noqa: F401
from app.features.evaluation.models import EvalRun, EvalRunItem, EvalTestCase, EvalTestSet  # noqa: F401
from app.features.knowledge.models import (  # noqa: F401
    KnowledgeBase,
    KnowledgeBibEntry,
    KnowledgeDocument,
    RagPendingDeletion,
)
from app.features.media.models import AvatarModel, BackgroundImage  # noqa: F401
from app.features.pages.models import SitePage, SitePageRevision, SitePageTranslation  # noqa: F401
from app.features.projects.models import Project  # noqa: F401
from app.features.pronunciation.models import PronunciationEntry  # noqa: F401
from app.features.site_settings.models import SiteSettings  # noqa: F401
from app.features.users.models import User  # noqa: F401
