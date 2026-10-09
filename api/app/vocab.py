"""Controlled vocabularies shared by schema, seed, API validation and (later) the chatbot."""

from enum import StrEnum


class DietaryTag(StrEnum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    GLUTEN_FREE = "gluten_free"
    HALAL = "halal"
    DAIRY_FREE = "dairy_free"


class Allergen(StrEnum):
    GLUTEN = "gluten"
    DAIRY = "dairy"
    EGG = "egg"
    PEANUT = "peanut"
    TREE_NUT = "tree_nut"
    SOY = "soy"
    FISH = "fish"
    SHELLFISH = "shellfish"
    SESAME = "sesame"
    MUSTARD = "mustard"
    CELERY = "celery"
    SULPHITES = "sulphites"


class OrderStatus(StrEnum):
    RECEIVED = "received"
    PREPARING = "preparing"
    READY = "ready"
    SERVED = "served"
    CANCELLED = "cancelled"


DIETARY_TAGS = tuple(t.value for t in DietaryTag)
ALLERGENS = tuple(a.value for a in Allergen)
ORDER_STATUSES = tuple(s.value for s in OrderStatus)
