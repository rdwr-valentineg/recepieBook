"""Seed initial recipes from the WhatsApp chat on first run."""
import os
import shutil
import uuid
from datetime import datetime
from sqlalchemy.orm import Session

from db import Recipe
from config import settings


SHEPHERD_INGREDIENTS = """לפירה (שכבה עליונה):
• כרובית בינונית
• בצל ירוק
• מלח ופלפל גרוס

למילוי:
• 500 גרם בשר טחון
• 1 בצל קצוץ
• 2-3 גזרים קצוצים
• 2 גבעולי סלרי קצוצים
• 3 שיני שום טחונות
• 150-200 גרם פטריות שמפיניון חתוכות
• 1/2 כוס מרק ירקות (מים חמים + כף אבקת מרק)
• כף רסק עגבניות
• כמון, מלח ופלפל"""

SHEPHERD_INSTRUCTIONS = """1. מחממים את התנור ל-180-200 מעלות.
2. מרתיחים מים בסיר ומוסיפים את פרחי הכרובית המפורקים. מבשלים עד לריכוך, כ-25 דקות.
3. למילוי הבשר: מטגנים את הבצל כמעט עד להשחמה, מוסיפים שום, גזר ומטגנים עוד 3-4 דקות. מוסיפים את הפטריות והבשר ומטגנים עד שהבשר מתבשל והפטריות מצטמצמות.
4. מוסיפים רסק עגבניות ותבלינים ומטגנים עוד 2 דקות.
5. לפירה: מסננים את פרחי הכרובית מהמים וטוחנים לפירה חלק. מערבבים עם תבלינים.
6. בתבנית יוצרים שכבה אחידה של בשר ומעל שכבה אחידה של פירה הכרובית.
7. ניתן ליצור דוגמת פסים במזלג על פירה הכרובית.
8. אופים עד שהקצוות מזהיבות."""


SEED = [
    dict(title="טירמיסו של הביוקר", category="desserts",
         url="https://mobile.mako.co.il/food-recipes/recipes_column-desserts/Recipe-fc37509a6a9ba61027.htm",
         added_by="baseline", date="2024-06-16", notes="מתכון מהאתר של מאקו"),

    dict(title="ריבועי הריבה של מיקי שמו", category="pastries",
         url="https://www.hashulchan.co.il/%D7%9E%D7%AA%D7%9B%D7%95%D7%A0%D7%99%D7%9D/%D7%A8%D7%99%D7%91%D7%95%D7%A2%D7%99-%D7%94%D7%A8%D7%99%D7%91%D7%94-%D7%A9%D7%9C-%D7%9E%D7%99%D7%A7%D7%99-%D7%A9%D7%9E%D7%95/",
         added_by="baseline", date="2024-06-16"),

    dict(title="מתכון מ-Biscotti", category="pastries",
         url="https://www.biscotti.co.il/recipes/?ContentID=31282",
         added_by="baseline", date="2024-08-01"),

    dict(title="מתכון לילדים ממאקו", category="pastries",
         url="https://mobile.mako.co.il/food-cooking_magazine/kids-recipes/Recipe-d80b3bbbdc9be21006.htm",
         added_by="baseline", date="2024-08-01"),

    dict(title="פאי רועים", category="meat", url=None,
         added_by="baseline", date="2024-09-15",
         ingredients=SHEPHERD_INGREDIENTS, instructions=SHEPHERD_INSTRUCTIONS,
         notes="20 דקות הכנה, 5 מנות. דל פחמימה ✨ ללא גלוטן.",
         image_source="shepherd-pie.jpg"),

    dict(title="עוגת אוכמניות ושוקולד לבן", category="desserts",
         url="https://www.oogio.net/white_chocolate_blueberry_cake/amp/",
         added_by="baseline", date="2025-12-13"),

    dict(title="חיתוכיות ריבה ופירורים", category="pastries",
         url="https://kerenagam.co.il/%D7%97%D7%99%D7%AA%D7%95%D7%9B%D7%99%D7%95%D7%AA-%D7%A8%D7%99%D7%91%D7%AA-%D7%95%D7%A4%D7%99%D7%A8%D7%95%D7%A8%D7%99%D7%9D/",
         added_by="baseline", date="2025-12-30"),

    dict(title="פנקייקים כמו בבית", category="breakfast",
         url="https://www.krutit.co.il/%D7%A4%D7%A0%D7%A7%D7%99%D7%99%D7%A7%D7%99%D7%9D-%D7%9B%D7%9E%D7%95-%D7%91%D7%91%D7%99%D7%AA-%D7%94%D7%A4%D7%A0%D7%A7%D7%99%D7%99%D7%A7-%D7%A7%D7%9C%D7%99%D7%9D-%D7%9E%D7%90%D7%95%D7%93-%D7%9C%D7%94/",
         added_by="baseline", date="2026-01-31", notes="הפנקייק קלים מאוד להכנה"),

    dict(title="טארט גראטן תפוחי אדמה מופלא", category="stews",
         url="https://www.krutit.co.il/%d7%98%d7%90%d7%a8%d7%98-%d7%92%d7%a8%d7%90%d7%98%d7%9f-%d7%aa%d7%a4%d7%95%d7%97%d7%99-%d7%90%d7%93%d7%9e%d7%94-%d7%9e%d7%95%d7%a4%d7%9c%d7%90/",
         added_by="baseline", date="2026-02-12"),

    dict(title="רולדת תותים וקצפת כמו של פעם", category="desserts",
         url="https://www.lichtenstadt.com/2021/12/%D7%A8%D7%95%D7%9C%D7%93%D7%AA-%D7%AA%D7%95%D7%AA%D7%99%D7%9D-%D7%95%D7%A7%D7%A6%D7%A4%D7%AA-%D7%9B%D7%9E%D7%95-%D7%A9%D7%9C-%D7%A4%D7%A2%D7%9D/",
         added_by="baseline", date="2026-02-20"),

    dict(title="מתכון לפיתות", category="bread",
         url="https://thebaker.science/%D7%9E%D7%AA%D7%9B%D7%95%D7%9F-%D7%9C%D7%A4%D7%99%D7%AA%D7%95%D7%AA/",
         added_by="baseline", date="2026-02-20"),

    dict(title="טירמיסו של פודי", category="desserts",
         url="https://foody.co.il/foody_recipe/%D7%98%D7%99%D7%A8%D7%9E%D7%99%D7%A1%D7%95-%D7%9B%D7%96%D7%94-%D7%A9%D7%9E%D7%99%D7%99%D7%A9%D7%A8%D7%99%D7%9D-%D7%95%D7%9E%D7%99%D7%99%D7%A9%D7%A8%D7%99%D7%9D-%D7%95%D7%9E%D7%99%D7%99%D7%A9%D7%A8/",
         added_by="baseline", date="2026-03-25", notes='טירמיסו "כזה שמיישרים"'),

    dict(title="ברווני באסק צ׳יזקייק", category="desserts",
         url="https://parischezsharon.com/he/2026/03/brownie-basque-cheesecake.html",
         added_by="baseline", date="2026-03-31", notes="שילוב של ברווני וצ׳יזקייק בסגנון באסקי"),

    dict(title="מתכון מפייסבוק", category="other",
         url="https://www.facebook.com/share/p/17U8jvECtj/",
         added_by="baseline", date="2026-04-08", notes="יש למלא את פרטי המתכון"),

    dict(title="מתכון עוף ממאקו", category="meat",
         url="https://www.mako.co.il/food-recipes/recipes_column-chicken/Recipe-f150ada24245a91027.htm",
         added_by="baseline", date="2026-05-10"),
]


def _assets_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_assets")


def seed_if_empty(db: Session) -> int:
    """If the recipes table is empty, populate with the seed data."""
    existing = db.query(Recipe).count()
    if existing > 0:
        return 0

    images_dir = os.path.join(settings.data_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    inserted = 0
    for entry in SEED:
        recipe_id = f"seed_{uuid.uuid4().hex[:8]}"
        image_filename = None

        if "image_source" in entry:
            src = os.path.join(_assets_dir(), entry["image_source"])
            if os.path.isfile(src):
                ext = os.path.splitext(entry["image_source"])[1] or ".jpg"
                image_filename = f"{recipe_id}{ext}"
                shutil.copyfile(src, os.path.join(images_dir, image_filename))

        r = Recipe(
            id=recipe_id,
            title=entry["title"],
            category=entry["category"],
            url=entry.get("url"),
            ingredients=entry.get("ingredients", ""),
            instructions=entry.get("instructions", ""),
            notes=entry.get("notes", ""),
            added_by=entry.get("added_by", ""),
            date=entry.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
            image_filename=image_filename,
        )
        db.add(r)
        inserted += 1

    db.commit()
    return inserted
