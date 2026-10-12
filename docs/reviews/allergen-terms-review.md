# Allergen synonym table: review sheet

**Status: Reviewed and approved by user 2026-10-11 (soy sauce → soy + gluten).**

Source of truth: `api/app/chat/allergen_terms.py` (this sheet was generated from it). Reviewing takes about ten minutes: skim section 1 for wrong mappings, section 2 for cue phrases that are too eager or too shy, section 3 for terms I deliberately left unmapped.

## How the table is used

- Every `search_menu` call runs `widen(model_args, user_message, session_declared)`. It UNIONS the allergens found in the diner's own words (and any declared earlier in the session, and the model's `other_allergy_terms`) into `exclude_allergens`. It never removes an exclusion the model chose.
- A term is only looked up AFTER an allergy cue (section 2), or when the model passes it in `other_allergy_terms`. "Show me tofu dishes" declares nothing.
- A term that is not in the table becomes an `untracked_terms` entry. The tool result then tells the model: not tracked, tell the diner, offer `call_waiter`.
- Mapping is deliberately wide: over-excluding costs a missed recommendation, under-excluding is a safety failure.
- Declared allergens are stored per session (WP3) and reused on later turns; they also drive `allergen_conflict` on cart proposals.

## 1. Synonym -> allergen mappings

The `Questionable` column flags judgement calls. Everything else is a plain synonym.

| Term | Excludes | Questionable? | Note |
|---|---|---|---|
| peanut | peanut |  |  |
| peanuts | peanut |  |  |
| groundnut | peanut |  |  |
| groundnuts | peanut |  |  |
| monkey nut | peanut |  |  |
| monkey nuts | peanut |  |  |
| peanut butter | peanut |  |  |
| nut | peanut + tree_nut | YES | Wide on purpose: a diner who says 'nuts' gets peanut AND tree_nut excluded. Over-excludes legumes for someone who only reacts to tree nuts. |
| nuts | peanut + tree_nut | YES | As above. |
| nut allergy | peanut + tree_nut | YES | As above. |
| tree nut | tree_nut |  |  |
| tree nuts | tree_nut |  |  |
| treenut | tree_nut |  |  |
| treenuts | tree_nut |  |  |
| almond | tree_nut |  |  |
| almonds | tree_nut |  |  |
| walnut | tree_nut |  |  |
| walnuts | tree_nut |  |  |
| cashew | tree_nut |  |  |
| cashews | tree_nut |  |  |
| pistachio | tree_nut |  |  |
| pistachios | tree_nut |  |  |
| hazelnut | tree_nut |  |  |
| hazelnuts | tree_nut |  |  |
| pecan | tree_nut |  |  |
| pecans | tree_nut |  |  |
| macadamia | tree_nut |  |  |
| macadamias | tree_nut |  |  |
| brazil nut | tree_nut |  |  |
| brazil nuts | tree_nut |  |  |
| pine nut | tree_nut | YES | Botanically a seed; the kitchen doc lists pine nuts with tree nuts, so mapped. |
| pine nuts | tree_nut | YES | As above. |
| chestnut | tree_nut | YES | Not a botanical nut-allergy staple; mapped to tree_nut for safety. |
| chestnuts | tree_nut | YES | As above. |
| fish | fish |  |  |
| salmon | fish |  |  |
| tuna | fish |  |  |
| cod | fish |  |  |
| haddock | fish |  |  |
| trout | fish |  |  |
| mackerel | fish |  |  |
| sardine | fish |  |  |
| sardines | fish |  |  |
| anchovy | fish |  |  |
| anchovies | fish |  |  |
| fish sauce | fish |  |  |
| shellfish | shellfish |  |  |
| shell fish | shellfish |  |  |
| crustacean | shellfish |  |  |
| crustaceans | shellfish |  |  |
| prawn | shellfish |  |  |
| prawns | shellfish |  |  |
| shrimp | shellfish |  |  |
| shrimps | shellfish |  |  |
| crab | shellfish |  |  |
| crabs | shellfish |  |  |
| lobster | shellfish |  |  |
| lobsters | shellfish |  |  |
| crayfish | shellfish |  |  |
| langoustine | shellfish |  |  |
| scampi | shellfish |  |  |
| mollusc | shellfish | YES | As above. |
| molluscs | shellfish | YES | As above. |
| mollusk | shellfish | YES | As above. |
| mollusks | shellfish | YES | As above. |
| squid | shellfish | YES | Molluscs are mapped to shellfish because the menu data tags calamari as shellfish. |
| calamari | shellfish | YES | As above. |
| octopus | shellfish | YES | As above. |
| clam | shellfish | YES | As above. |
| clams | shellfish | YES | As above. |
| mussel | shellfish | YES | As above. |
| mussels | shellfish | YES | As above. |
| oyster | shellfish | YES | As above. |
| oysters | shellfish | YES | As above. |
| scallop | shellfish | YES | As above. |
| scallops | shellfish | YES | As above. |
| seafood | fish + shellfish | YES | Wide on purpose: fish AND shellfish. |
| sea food | fish + shellfish | YES | As above. |
| gluten | gluten |  |  |
| wheat | gluten |  |  |
| coeliac | gluten |  |  |
| celiac | gluten |  |  |
| coeliac disease | gluten |  |  |
| celiac disease | gluten |  |  |
| barley | gluten |  |  |
| rye | gluten |  |  |
| spelt | gluten |  |  |
| semolina | gluten |  |  |
| dairy | dairy |  |  |
| milk | dairy |  |  |
| cow's milk | dairy |  |  |
| cows milk | dairy |  |  |
| lactose | dairy | YES | Lactose intolerance is not a milk allergy, but the exclusion we can offer is the same (dairy). |
| cheese | dairy | YES | A single dairy product widened to the whole dairy group (safe direction). |
| cream | dairy | YES | As above. |
| butter | dairy | YES | As above. |
| yogurt | dairy |  |  |
| yoghurt | dairy |  |  |
| whey | dairy |  |  |
| casein | dairy |  |  |
| ghee | dairy |  |  |
| egg | egg |  |  |
| eggs | egg |  |  |
| sesame | sesame |  |  |
| sesame seed | sesame |  |  |
| sesame seeds | sesame |  |  |
| tahini | sesame |  |  |
| soy | soy |  |  |
| soya | soy |  |  |
| soybean | soy |  |  |
| soybeans | soy |  |  |
| soy sauce | soy + gluten | YES | Most soy sauce contains wheat, so gluten is excluded too. Approved by user 2026-10-11. |
| tofu | soy | YES | Only consulted after an allergy cue ('allergic to tofu'). 'show me tofu dishes' is NOT a declaration. Tofu is soy, so excluding soy is right. |
| tempeh | soy |  |  |
| edamame | soy |  |  |
| miso | soy |  |  |
| mustard | mustard |  |  |
| celery | celery |  |  |
| celeriac | celery | YES | Same plant family; EU labelling treats celeriac as celery. |
| sulphite | sulphites |  |  |
| sulphites | sulphites |  |  |
| sulfite | sulphites |  |  |
| sulfites | sulphites |  |  |
| sulphur dioxide | sulphites |  |  |
| sulfur dioxide | sulphites |  |  |

Term count: 125 across 12 allergens.

## 2. Cue phrases (what counts as an allergy declaration)

Matching is case-insensitive, apostrophes normalised. Anything after a cue is split on commas, `and`, `or`, `&`, `/` and parsed up to the first stop word (`but`, `what`, `can`, `I`, `please`, ...).

| Cue (regex) | Example | Questionable? | Note |
|---|---|---|---|
| `allergic\s+to` | I'm allergic to nuts |  |  |
| `allergy\s+to` | an allergy to shellfish |  |  |
| `allergies\s+to` | allergies to eggs |  |  |
| `allerg(?:y\|ies)\s*(?::\|-\|is\|are\|include\|includes)` |  |  |  |
| `intoleran(?:t\|ce)\s+to` | intolerant to dairy |  |  |
| `sensitiv(?:e\|ity)\s+to` | sensitive to sulphites | YES | Sensitivity may be mild; still treated as an allergy (safe direction). |
| `react(?:s)?\s+to` | reacts to sesame | YES | Looser wording than 'allergic'. |
| `reaction\s+to` | a reaction to prawns | YES | Looser wording than 'allergic'. |
| `can'?t\s+(?:eat\|have)` | I can't eat shellfish |  |  |
| `cannot\s+(?:eat\|have)` | I can't eat shellfish |  |  |
| `can\s+not\s+(?:eat\|have)` | I can't eat shellfish |  |  |
| `cant\s+(?:eat\|have)` | I can't eat shellfish | YES | Misspelling without apostrophe. |
| `unable\s+to\s+eat` |  |  |  |
| `not\s+able\s+to\s+eat` |  |  |  |
| `must\s+not\s+eat` |  |  |  |
| `mustn'?t\s+eat` |  |  |  |
| `not\s+allowed\s+(?:to\s+eat\s+)?` |  | YES | Rare phrasing. |
| `(?:must\|have\s+to\|has\s+to\|need\s+to\|needs\s+to)\s+avoid` |  | YES | Bare 'avoid' is not a cue (preference); needs must/have to/need to. |
| `<term> allergy / allergic / intolerant / intolerance` | I have a nut allergy; lactose intolerant | YES | The word before the term must declare a condition (a, an, have, has, got, with, severe, bad, mild, serious, my, I, he, she...) or the term starts the message. So 'what is your nut allergy policy' is NOT a declaration. |
| `coeliac` / `celiac` anywhere | I'm coeliac | | Implies gluten with no further term. Ignored when negated. |
| `no <term> please` | no nuts please | YES | Known terms only widen the filter. Unknown terms are IGNORED (so 'no onions please' does not trigger 'not tracked'). Judgement: usually a preference or an allergy shorthand; widening is harmless. |
| generic: `allergic` not followed by `to`; `have/has/got (a/an/some/severe/food) allerg(y/ies)` | I'm allergic, what can I eat? | | Sets the cue (turn is allergen-sensitive) without any term. |

Negation: a cue preceded within ~25 characters by `not`, `n't`, `no`, `never`, `without` is ignored ("I'm not allergic to peanuts").

Words that mean nothing specific after a cue and are skipped: anything, everything, food, foods, it, lot, lots, nothing, one, some, someone, something, stuff, that, them, these, things, this, those.

Allergen-topic detection (marks a turn allergen-sensitive, which forces the DB-built allergen notice and the unsafe-language check) is broader: any of allergy/allergic/intolerant/coeliac/anaphylaxis/epipen/gluten/dairy/lactose/vegan/vegetarian/halal/nut(s)/peanut/shellfish/sesame/dietary/safe, an allergy cue, or any table term longer than 3 letters (so asking about 'salmon' or 'cheese' also gets the notice). Questionable: it is generous on purpose; the cost is an extra notice box.

## 3. Deliberately NOT mapped (become `untracked_terms`)

| Term | Why | Questionable? |
|---|---|---|
| coconut | kitchen policy: not treated as a tree nut; individual reactions vary | YES |
| nutmeg | kitchen policy: not a tree nut |  |
| wine | could be sulphites or alcohol; ask the waiter rather than guess | YES |
| oats | oat allergy is not gluten; the oat latte is tagged gluten by the kitchen | YES |
| garlic | not one of the 12 tracked allergens |  |
| onion | not one of the 12 tracked allergens |  |
| kiwi | not one of the 12 tracked allergens |  |
| lupin | EU allergen but not tracked in this menu |  |
| alcohol | not an allergen in the menu data |  |

Coconut and nutmeg follow the kitchen's own policy page (not treated as tree nuts). Oats and wine are the two I would most like a second opinion on: both are common, both could reasonably map to gluten / sulphites.

## 4. Known limits

- English only; no typo tolerance ("pea nuts", "shelfish" fall through to untracked or nothing).
- Cues are phrase-based. "My friend can't eat nuts" counts as the diner's declaration (over-protective by design).
- A declaration is never un-declared within a session ("actually I'm fine with nuts" does not clear it). Clearing needs a human decision, so it goes through the waiter.
- Compound dishes ("satay sauce") are not expanded; the menu's structured allergens are the authority for dishes, this table only reads the diner's words.

