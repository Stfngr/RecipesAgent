from dataclasses import dataclass
from html.parser import HTMLParser
import re


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "li", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "li", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = PlainText()
    parser.feed(value)
    return "\n".join(
        line for part in "".join(parser.parts).splitlines()
        if (line := " ".join(part.split()))
    )


@dataclass
class Recipe:
    id: int
    title: str
    vegetarian: bool
    ingredients: list[str]
    instructions: list[str]
    ready_minutes: int | None = None
    prep_minutes: int | None = None
    cooking_minutes: int | None = None
    servings: int | None = None
    source_url: str = ""
    source_name: str = ""
    license: str = ""

    def __post_init__(self):
        if type(self.id) is not int or self.id <= 0:
            raise ValueError("Recipe ID must be a positive integer")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("Recipe title missing")
        if type(self.vegetarian) is not bool:
            raise ValueError("Recipe vegetarian classification missing")
        for items in (self.ingredients, self.instructions):
            if not isinstance(items, list) or not items or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise ValueError("Recipe ingredients or instructions missing")
        for value in (self.ready_minutes, self.prep_minutes, self.cooking_minutes, self.servings):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Invalid recipe time or servings")
        for value in (self.source_url, self.source_name, self.license):
            if not isinstance(value, str):
                raise ValueError("Invalid recipe attribution")

    @classmethod
    def from_api(cls, data: dict) -> "Recipe":
        ingredients = [plain_text(item["original"]) for item in data.get("extendedIngredients", [])]
        instructions = []
        for section in data.get("analyzedInstructions") or []:
            if section.get("name"):
                instructions.append(plain_text(section["name"]))
            instructions.extend(plain_text(step["step"]) for step in section.get("steps", []))
        if not instructions:
            instructions = plain_text(data.get("instructions") or "").splitlines()

        def number(key):
            value = data.get(key)
            return value if type(value) is int and value >= 0 else None

        return cls(
            id=data["id"], title=plain_text(data["title"]), vegetarian=data["vegetarian"],
            ingredients=ingredients, instructions=instructions,
            ready_minutes=number("readyInMinutes"), prep_minutes=number("preparationMinutes"),
            cooking_minutes=number("cookingMinutes"), servings=number("servings"),
            source_url=data.get("sourceUrl") or data.get("spoonacularSourceUrl") or "",
            source_name=plain_text(data.get("creditsText") or data.get("sourceName") or ""),
            license=data.get("license") or "",
        )


def split_message(text: str) -> list[str]:
    # 2,000 code points fit Telegram's 4,096 UTF-16-unit limit even for emoji.
    chunks = []
    while len(text) > 2000:
        cut = text.rfind("\n", 0, 2001)
        if cut <= 0:
            cut = 2000
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def summary(recipes: list[Recipe], dessert: bool, language: str, trigger: str, minutes: int) -> list[str]:
    de = language == "de"
    heading = "Heutige Rezeptauswahl:" if de else "Today's recipes:"
    status = ("JA" if dessert else "NEIN") if de else ("YES" if dessert else "NO")
    dessert_label = "Dessert heute" if de else "Dessert today"
    hint = (
        f"Auswahl: {trigger} <Nummer>; {trigger} 0 fuer keine Auswahl "
        f"(aktiv fuer {minutes} Minuten)"
        if de else f"Select: {trigger} <number>; {trigger} 0 for no selection "
        f"(active for {minutes} minutes)"
    )
    titles = [f"{index}. {' '.join(recipe.title.split())}" for index, recipe in enumerate(recipes, 1)]
    return split_message("\n".join([heading, *titles, "", f"{dessert_label}: {status}", "", hint]))


def details(recipe: Recipe, language: str, automatic: bool) -> list[str]:
    de = language == "de"
    heading = ("Automatische Auswahl" if automatic else "Ausgewaehltes Rezept") if de else (
        "Automatic selection" if automatic else "Selected recipe"
    )
    lines = [f"{heading}: {recipe.title}"]
    for label, value in (
        ("Portionen" if de else "Servings", recipe.servings),
        ("Gesamtzeit (Min.)" if de else "Total time (min)", recipe.ready_minutes),
        ("Vorbereitung (Min.)" if de else "Preparation (min)", recipe.prep_minutes),
        ("Kochzeit (Min.)" if de else "Cooking time (min)", recipe.cooking_minutes),
    ):
        if value is not None:
            lines.append(f"{label}: {value}")
    lines.extend(["", "Zutaten:" if de else "Ingredients:"])
    lines.extend(f"- {item}" for item in recipe.ingredients)
    lines.extend(["", "Zubereitung:" if de else "Instructions:"])
    lines.extend(f"{index}. {step}" for index, step in enumerate(recipe.instructions, 1))
    if recipe.source_name:
        lines.extend(["", recipe.source_name])
    if recipe.license:
        lines.append(recipe.license)
    if recipe.source_url:
        lines.append(recipe.source_url)
    return split_message("\n".join(lines))


def skipped(language: str) -> list[str]:
    return ["Keine Auswahl fuer heute. Bis morgen." if language == "de"
            else "No selection for today. See you tomorrow."]


def parse_selection(text: str, trigger: str, count: int) -> int | None:
    match = re.fullmatch(re.escape(trigger) + r"\s+([0-9]{1,3})\s*", text)
    if match and 1 <= int(match[1]) <= count:
        return int(match[1]) - 1
    return None


def is_skip_command(text: str, trigger: str) -> bool:
    return text == f"{trigger} 0"
