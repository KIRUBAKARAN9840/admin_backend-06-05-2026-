import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateTable

# Add app to path
sys.path.append(os.getcwd())

from app.models.nutrition_models import NutritionConsultationForm, Base
from app.config.settings import settings

# Database URL from settings
DATABASE_URL = settings.DATABASE_URL
# Convert mysql:// to mysql+pymysql:// if needed
if DATABASE_URL.startswith("mysql://") and "pymysql" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://")

engine = create_engine(DATABASE_URL)

# Create the table
try:
    with engine.begin() as conn:
        # Check if table exists
        # result = conn.execute(f"SHOW TABLES IN nutrition LIKE 'nutrition_consultation_form'")
        # if not result.fetchone():
        print("Creating table nutrition_consultation_form in schema nutrition")
        NutritionConsultationForm.__table__.create(conn, checkfirst=True)
        print("Table creation check completed.")
except Exception as e:
    print(f"Error: {e}")
