import sys
import os

# Add the backend app directory to sys.path
sys.path.append("/Users/admin/Desktop/fittbot_client_new_ui/admin-01-05/backend/production_backend")

# Set dummy env vars to avoid loading errors if needed
os.environ.setdefault("ENV", "production")

from app.models.database import get_db
from app.models.fittbot_models.gym import Gym

def inspect():
    db = next(get_db())
    try:
        # Total gyms in database
        total_in_db = db.query(Gym).count()
        print(f"Total gyms in DB (all rows): {total_in_db}")

        # Unique types
        print("\nGym type counts:")
        for t_row in db.query(Gym.type).distinct().all():
            t = t_row[0]
            count = db.query(Gym).filter(Gym.type == t).count() if t is not None else db.query(Gym).filter(Gym.type.is_(None)).count()
            print(f"Type: {t!r} -> Count: {count}")

        # Count of gyms with gym_id != 1
        total_ex_1 = db.query(Gym).filter(Gym.gym_id != 1).count()
        print(f"\nTotal gyms excluding gym_id=1: {total_ex_1}")

        # Count of gyms where fittbot_verified is True
        verified_count = db.query(Gym).filter(Gym.fittbot_verified == True).count()
        print(f"Total verified gyms (fittbot_verified == True): {verified_count}")
        
    finally:
        db.close()

if __name__ == "__main__":
    inspect()
