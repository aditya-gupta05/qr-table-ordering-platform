# Retrieval eval set v1: review sheet

Status: FROZEN. 65 queries. Source of truth: `eval/datasets/retrieval_v1.jsonl`. This file is generated: `cd api && uv run python ../eval/retrieval/run_retrieval_eval.py --validate --review`.

## How to review (about 15 minutes)

For each row check three things:
1. **Phrasing.** Would a real diner type this? Short, casual, typos are fine. It should not echo the document's wording.
2. **Answerable.** Can the corpus really answer it (or, for `no_answer`, genuinely not)? Grep `api/corpus/` when unsure.
3. **Labels complete.** Is there another section (a second doc, the PDF, the dish guide) that also answers it and is missing from the evidence? Missing labels make good retrieval look bad.

To change something: edit the row in `eval/datasets/retrieval_v1.jsonl` (query, `relevant_menu_item_names`, or add an `evidence` entry with a verbatim `quote` from `api/corpus/*.md` or the PDF text, `grade` 2 = directly answers, 1 = partially). Or just write a note in the **Your notes** column below and tell the agent. Then run `--validate` (it fails loudly on any quote that does not resolve). Do not edit `split` by hand.

Flags: **J** = query/evidence token Jaccard > 0.5 (leakage risk); **C** = more than 80% of the query's words appear in an evidence quote; **?** = labelling judgement call (see Notes).

| id | cat | split | query | filters | expected items | evidence (grade, doc: quote) | flags / notes | Your notes |
|---|---|---|---|---|---|---|---|---|
| men-01 | menu_lookup | test | whats in the pad thai |  | Pad Thai | (2, dish-guide, group prep) Rice noodles are stir-fried in a very hot wok with prawns, egg, bean …<br>(1, menu-pdf, group prep) Stir-fried rice noodles with prawns, egg, bean sprouts, tamarind |  | |
| men-02 | menu_lookup | dev | how spicy is the thai green curry |  | Thai Green Curry | (2, dish-guide, group spice) It is the hottest dish on the menu, at spice level three.<br>(2, faq, group spice) Our Thai Green Curry and Spicy Tofu Stir-Fry are the spiciest dishes … |  | |
| men-03 | menu_lookup | dev | price of the miso salmon |  | Miso Glazed Salmon | (2, menu-pdf, group price) Miso Glazed Salmon $28.00<br>(1, dish-guide, group price) It is the most expensive main on the menu, and the portion is generou… |  | |
| men-04 | menu_lookup | test | is there a burger |  | Beef Burger | (1, dish-guide, group patty) A smashed beef patty is seared on the flat-top, topped with melted ch…<br>(1, menu-pdf, group patty) Smashed beef patty, cheddar, pickles and house sauce on a brioche bun. |  | |
| men-05 | menu_lookup | test | which dessert has mango in it |  | Mango Sorbet | (2, dish-guide) Two scoops of sorbet are made from ripe Alphonso mangoes, sugar and a… |  | |
| men-06 | menu_lookup | test | what dish comes with kimchi |  | Short Rib Rice Bowl, House Kimchi | (2, dish-guide) served on steamed rice with kimchi and a fried egg with a runny yolk<br>(1, dish-guide) It is the same kimchi served with the Short Rib Rice Bowl. |  | |
| men-07 | menu_lookup | dev | what do u drink with really hot curry |  | Mango Lassi | (2, dish-guide, group pairing) It is the drink we recommend with the hottest dishes, such as the Tha…<br>(2, menu-pdf, group pairing) Mango Lassi is the classic partner to the hottest curries because the…<br>(1, faq, group pairing) the kitchen can send out extra rice, cucumber or a mango lassi | **?** Also answerable from the Thai Green Curry dish-guide sentence 'Extra rice or a Mango Lassi calms the heat.' (not labelled). | |
| men-08 | menu_lookup | dev | any soups? |  | Tomato Basil Soup, Butternut Squash Soup | (1, dish-guide) Tomatoes are slow-roasted with garlic and thyme for two hours, blende…<br>(1, dish-guide) Butternut squash is roasted until caramelised, simmered with stock an… |  | |
| men-09 | menu_lookup | dev | how long will the risotto take to come out |  | Mushroom Risotto | (2, dish-guide) It is made to order, so expect about twenty minutes. |  | |
| men-10 | menu_lookup | dev | whats the lamb dish like |  | Lamb Kofta Plate | (2, dish-guide, group lamb) Minced lamb is spiced with cumin, coriander and a little cinnamon, sh…<br>(1, menu-pdf, group lamb) Spiced lamb kofta, saffron rice, cucumber yoghurt and |  | |
| men-11 | menu_lookup | test | tell me about the kulfi |  | Pistachio Kulfi | (2, dish-guide, group kulfi) Milk is reduced slowly with cardamom, mixed with ground pistachio and…<br>(1, menu-pdf, group kulfi) Dense, slow-set Indian ice cream flavoured with cardamom and |  | |
| men-12 | menu_lookup | dev | what do you get with fish n chips |  | Fish and Chips | (2, dish-guide, group plate) then served with thick-cut chips, mushy peas and house tartare sauce<br>(2, menu-pdf, group plate) Beer-battered cod, thick-cut chips, mushy peas and tartare |  | |
| men-13 | menu_lookup | dev | do u have hummus |  | Roasted Cauliflower Hummus | (2, dish-guide) Chickpeas are blended until whipped and pale, then topped with caulif… |  | |
| men-15 | menu_lookup | test | whats the sauce on the gnocchi |  | Pesto Gnocchi | (2, dish-guide, group sauce) tossed in basil and pine-nut pesto with parmesan and blistered cherry…<br>(1, menu-pdf, group sauce) Potato gnocchi in basil and pine-nut pesto with |  | |
| die-01 | dietary_allergen | test | something vegan to start | dietary=vegan; scope=menu | Edamame, Roasted Cauliflower Hummus, Satay Tofu Skewers, Butternut Squash Soup |  | **?** Vegan starters per menu data. | |
| die-02 | dietary_allergen | dev | vegan sweet thing? | dietary=vegan; scope=menu | Mango Sorbet, Coconut Rice Pudding |  |  | |
| die-03 | dietary_allergen | dev | gluten free main please | dietary=gluten_free; scope=menu | Chicken Tikka Masala, Mushroom Risotto, Lemon Herb Roast Chicken, Thai Green Curry |  | **?** 'main' = Mains + Bowls & Curries; all gluten_free items in those categories (4). Derived by SQL, see REVIEW.md. | |
| die-04 | dietary_allergen | dev | dessert with no dairy or gluten | exclude_allergens=dairy,gluten; scope=menu | Mango Sorbet, Coconut Rice Pudding |  |  | |
| die-05 | dietary_allergen | test | halal starters, not soup | dietary=halal; scope=menu | Chicken Satay, Edamame, Roasted Cauliflower Hummus, Satay Tofu Skewers |  | **?** Known limitation: negation ('not soup') is not honoured by embeddings; Butternut Squash Soup is halal but excluded from labels. Phase 5's LLM handles negation. | |
| die-06 | dietary_allergen | test | what about gluten free desserts | dietary=gluten_free; scope=menu | Mango Sorbet, Flourless Chocolate Torte, Matcha Panna Cotta, Coconut Rice Pudding, Pistachio Kulfi |  | **?** Terse follow-up style. 5 relevant items, so recall@5 can only be perfect if all fill the top 5. | |
| die-07 | dietary_allergen | dev | veggie main without nuts | dietary=vegetarian; exclude_allergens=tree_nut,peanut; scope=menu | Margherita Pizza, Mushroom Risotto, Vegan Buddha Bowl, Spicy Tofu Stir-Fry |  | **?** Trap: Pesto Gnocchi (vegetarian, pine nuts) must not appear. | |
| die-08 | dietary_allergen | dev | my kid has a tree nut allergy, any fruity dessert | exclude_allergens=tree_nut; scope=menu | Mango Sorbet, Spiced Apple Crumble |  | **?** Lexical trap: Spiced Apple Crumble mentions nutmeg (a spice, not a tree nut). | |
| die-09 | dietary_allergen | test | spicy but no soy | exclude_allergens=soy; scope=menu | Thai Green Curry, Chicken Tikka Masala, House Kimchi, Garlic Greens, Butternut Squash Soup, Pad Thai, Lamb Kofta Plate |  | **?** Spicy = spice level >= 1 among soy-free dishes (drinks excluded: Ginger Beer is spice 1 but not a dish). Labels derived by SQL with the row's own filters, see REVIEW.md. | |
| die-10 | dietary_allergen | test | vegan and gluten free soup or starter | dietary=vegan,gluten_free; scope=menu | Edamame, Roasted Cauliflower Hummus, Butternut Squash Soup |  |  | |
| die-11 | dietary_allergen | dev | egg allergy, what mains can i get | exclude_allergens=egg; scope=menu | Margherita Pizza, Lemon Herb Roast Chicken, Miso Glazed Salmon, Lamb Kofta Plate, Chicken Tikka Masala, Mushroom Risotto, Spicy Tofu Stir-Fry, Thai Green Curry, Vegan Buddha Bowl |  | **?** 'mains' = Mains + Bowls & Curries (same rule as die-03/die-07); 9 egg-free items. Derived by SQL, see REVIEW.md. | |
| die-12 | dietary_allergen | dev | any drink thats dairy and gluten free | exclude_allergens=dairy,gluten; scope=menu | Fresh Lemonade, Iced Jasmine Tea, Ginger Beer, Pinot Grigio (glass) |  | **?** 'drink' = Drinks category; all dairy-free and gluten-free drinks (4, includes the wine). Derived by SQL, see REVIEW.md. | |
| faq-01 | faq_policy | test | what time do you close saturday night |  |  | (2, faq, group hours) Monday to Thursday we serve from 11:30 to 22:00, Friday and Saturday …<br>(1, menu-pdf, group hours) Friday and Saturday 11:30 to 23:00 and Sunday 12:00 to 21:00 |  | |
| faq-02 | faq_policy | test | what about sunday hours |  |  | (2, faq, group hours) Monday to Thursday we serve from 11:30 to 22:00, Friday and Saturday …<br>(1, menu-pdf, group hours) Friday and Saturday 11:30 to 23:00 and Sunday 12:00 to 21:00 | **?** Terse follow-up style ('what about ...'). | |
| faq-03 | faq_policy | dev | can i bring my own bottle of wine and is there a fee |  |  | (2, policies, group corkage) Guests are welcome to bring wine to Lantern Kitchen for a corkage fee…<br>(1, faq, group corkage) Guests may bring their own wine; our corkage terms are on the policie…<br>(1, menu-pdf, group corkage) corkage $25.00 per bottle |  | |
| faq-04 | faq_policy | dev | need a table for 10, any special rules |  |  | (2, faq) Groups of eight or more are treated as large parties and are subject …<br>(2, policies) To confirm a large party booking we take a card number as a guarantee. |  | |
| faq-05 | faq_policy | test | do i have to add a tip or is it already on there |  |  | (2, faq) Not for parties under eight. For smaller tables, gratuity is at your …<br>(2, policies) Gratuity is not included in the bill for parties of seven guests or f… |  | |
| faq-06 | faq_policy | dev | can i bring my dog |  |  | (2, policies, group dogs) Pets on a lead may sit with their owners on the patio, which is open …<br>(2, faq, group dogs) Dogs on a lead are welcome on the patio only. |  | |
| faq-07 | faq_policy | dev | where do i park |  |  | (2, faq) Metered street parking on Harbour Street is free after 18:00 and on S…<br>(2, faq) The Harbour Car Park, a two-minute walk away on Pier Lane, charges a … |  | |
| faq-08 | faq_policy | dev | do you do delivery or takeaway |  |  | (2, faq, group no_delivery) No. We do not offer delivery ourselves and we do not list on third-pa…<br>(2, faq) Phone us on (555) 014-2290 or order at the counter.<br>(1, faq, group no_delivery) QR ordering from the table is for dining in only and cannot be used f… |  | |
| faq-09 | faq_policy | dev | our group of 12 might cancel, what do we owe |  |  | (2, policies) A cancellation or a reduction of more than two guests made more than …<br>(2, policies) A cancellation made within twenty-four hours, or a no-show, will be c… |  | |
| faq-10 | faq_policy | dev | wifi password? |  |  | (2, faq) Yes, a free guest network called LanternGuest.<br>(2, faq) The password changes weekly and is written on the small chalkboard by… |  | |
| faq-11 | faq_policy | test | is it wheelchair friendly |  |  | (2, faq, group entrance) Yes. The main entrance is level with the pavement, the dining room is…<br>(2, policies) Tables T3 and T7 have chairs that can be removed, which allows a whee…<br>(1, policies, group entrance) The main entrance is level with the pavement and has a push-button do… |  | |
| faq-12 | faq_policy | dev | how long does a gift voucher last |  |  | (2, faq) Gift cards are valid for three years from the purchase date |  | |
| faq-13 | faq_policy | test | can we split the check 4 ways |  |  | (2, faq) Yes, evenly or by item, up to six ways. |  | |
| faq-14 | faq_policy | test | ordered the wrong thing a few mins ago, can it be cancelled |  |  | (2, faq) Within about a minute, while the status still says received, ask a me…<br>(2, faq) Once the status is preparing, the kitchen has started cooking and we … |  | |
| faq-15 | faq_policy | dev | do you keep what i type to the chatbot |  |  | (2, policies) Questions you type into the assistant on the ordering page are proces… |  | |
| kit-01 | kitchen_practices | dev | are the fries cooked separately from the fried fish |  | Shoestring Fries | (2, allergen-kitchen-practices, group fryer) We also have a smaller fryer that is reserved for gluten-free items o…<br>(2, dish-guide, group fryer) fried to a pale gold in the dedicated gluten-free fryer<br>(1, menu-pdf, group fryer) Cooked in a dedicated gluten-free fryer. | **?** 'fries' vs 'chips': Fish and Chips uses thick-cut chips in the shared fryer; both readings are covered by the practices quote. | |
| kit-02 | kitchen_practices | dev | what about coeliacs, is it ok for them |  |  | (2, allergen-kitchen-practices, group coeliac) We do not claim that our kitchen is a gluten-free kitchen. People wit…<br>(1, faq, group coeliac) people with coeliac disease should read the kitchen practices page an… | **?** Terse follow-up style. Also relevant: the gluten-free preparation section of practices (not labelled). | |
| kit-03 | kitchen_practices | test | do you cook with peanut oil |  |  | (2, allergen-kitchen-practices, group peanut_oil) We do not use peanut oil in any fryer.<br>(1, allergen-kitchen-practices, group peanut_oil) Fryer oil is a blend of sunflower and rapeseed oil. |  | |
| kit-04 | kitchen_practices | dev | is the halal chicken cooked next to the pork |  |  | (2, allergen-kitchen-practices, group pork) Pork, bacon and non-certified beef are cooked in this kitchen, on a s…<br>(2, allergen-kitchen-practices) Nevertheless, we are not a halal-certified restaurant, and the halal …<br>(2, faq, group pork) We also cook pork in the same kitchen, with separate utensils, so we … |  | |
| kit-05 | kitchen_practices | dev | whats the difference between vegan and vegetarian at your place |  |  | (2, allergen-kitchen-practices) We use the vegetarian tag for dishes that contain no meat, poultry, f…<br>(2, allergen-kitchen-practices) We use the vegan tag for dishes that contain no animal products at al… |  | |
| kit-06 | kitchen_practices | test | if i write no nuts in the order note will the kitchen see it |  |  | (2, allergen-kitchen-practices) If you add a note to your order through the QR menu, for example "no …<br>(2, allergen-kitchen-practices) However, the kitchen screen is a convenience and not an allergy system |  | |
| kit-07 | kitchen_practices | test | sesame allergy, where is sesame used in the kitchen |  |  | (2, allergen-kitchen-practices, group sesame) Sesame is present in several forms in the kitchen, including toasted …<br>(1, allergen-kitchen-practices, group sesame) finishes some dishes with a splash of toasted sesame oil, soy sauce a… |  | |
| kit-08 | kitchen_practices | dev | how is an allergy order handled differently |  |  | (2, allergen-kitchen-practices) A dish for an allergy is prepared at a cleaned station, with a clean …<br>(2, allergen-kitchen-practices) When you tell your server about an allergy, the server writes the all… |  | |
| pdf-01 | pdf_only | dev | do you have a lunch deal |  |  | (2, menu-pdf, group lunch) Two courses for $24.00: choose a starter and a main from the lists be…<br>(1, faq, group lunch) The best time is between 11:30 and 14:30 on weekdays, when our set lu… |  | |
| pdf-02 | pdf_only | test | what wine goes with the salmon |  |  | (2, menu-pdf) Miso Glazed Salmon. A dry Riesling or a light Pinot Noir. | **?** Wine pairings exist only in the printed menu PDF. | |
| pdf-03 | pdf_only | dev | whats the chefs tasting thing and how much per person |  |  | (2, menu-pdf) Chef's tasting plate, for tables of two or more, is available on requ…<br>(2, menu-pdf) It is a seven-dish sequence chosen by the kitchen and cannot be order… |  | |
| pdf-04 | pdf_only | dev | banquet style set menu for a group of 5? |  |  | (2, menu-pdf) A sharing menu for the whole table, $42.00 per person, for tables of … |  | |
| pdf-05 | pdf_only | test | can i swap dishes on the weekday lunch |  |  | (2, menu-pdf) Substitutions are not available on the set lunch, and it cannot be co…<br>(1, menu-pdf) The set lunch is ordered from your server and is not on the QR menu. |  | |
| pdf-06 | pdf_only | dev | how much food should four of us order |  |  | (2, menu-pdf, group table_of_four) A table of four usually does well with three starters, three mains an…<br>(1, menu-pdf, group table_of_four) order a few starters, one or two bowls or mains, a side of rice |  | |
| com-01 | comparison | dev | which is hotter the green curry or the tofu stir fry |  | Thai Green Curry, Spicy Tofu Stir-Fry | (2, dish-guide, group curry_heat) It is the hottest dish on the menu, at spice level three.<br>(2, dish-guide) At spice level three it is as hot as the Thai Green Curry.<br>(1, faq, group curry_heat) Our Thai Green Curry and Spicy Tofu Stir-Fry are the spiciest dishes … |  | |
| com-02 | comparison | dev | difference between the chicken satay and the tofu one |  | Chicken Satay, Satay Tofu Skewers | (2, dish-guide) Thigh meat is marinated overnight in turmeric, lemongrass and coconut…<br>(2, dish-guide, group tofu_satay) Firm tofu is pressed, marinated in turmeric and lemongrass, and grill…<br>(1, menu-pdf, group tofu_satay) The plant-based twin of our Chicken Satay. |  | |
| com-03 | comparison | test | which is richer, the short rib bowl or the tikka masala |  | Short Rib Rice Bowl, Chicken Tikka Masala | (2, dish-guide) It is one of our richest dishes and one of the largest.<br>(2, dish-guide) The sauce is medium spicy, at level two, and rich rather than hot. |  | |
| com-04 | comparison | test | do the tikka masala and the gnocchi have nuts |  | Chicken Tikka Masala, Pesto Gnocchi | (2, dish-guide) finished with a little ground almond<br>(2, dish-guide) Because the pesto is made with pine nuts, it is one of the dishes the… |  | |
| no_-01 | no_answer | dev | any live bands on tuesday nights |  |  |  | **?** Absent: corpus only mentions background instrumental music (FAQ); near-miss on music and Tuesday.; no answer expected | |
| no_-02 | no_answer | test | do you have a loyalty card or points scheme |  |  |  | **?** Absent (grep: loyalty, points).; no answer expected | |
| no_-03 | no_answer | dev | is there an ev charging spot |  |  |  | **?** Absent (grep: charging, electric).; no answer expected | |
| no_-04 | no_answer | dev | when does brunch start on weekends |  |  |  | **?** Absent (grep: brunch). Weekend hours exist but no brunch.; no answer expected | |
| no_-05 | no_answer | test | how many calories in the pad thai |  |  |  | **?** Absent (grep: calori, nutrition). Pad Thai itself is in the corpus: near-miss.; no answer expected | |
| no_-06 | no_answer | dev | is there a play area for my toddler |  |  |  | **?** Absent. High chairs and a children's cooking evening exist: near-miss.; no answer expected | |

## Changes applied (dataset v2, after the user review)

FROZEN at 2026-10-11T23:03:27.326520+00:00, test_sha256 `233cb205a52b7753b0ddfcb238f1fd6a83e7d96dadc4a6b43c42003332129f2d`. The hash covers every test row (query, filters, scope, labels, groups, notes); the harness refuses
`--split test` if any of them changes.

1. **Menu-only scope accepted** for all dietary/allergen rows (`scope=["menu"]`). Why: with docs in scope the
   FAQ/practices chunks crowd out the structured items (dev dietary recall 0.036 in the first run). The
   both-scope result is reported as an ablation (`--ignore-row-scope`).
2. **men-14 dropped** ("whats the cheapest thing i can eat"). Price ordering is an aggregation (`ORDER BY price
   LIMIT 1`) for SQL, not semantic retrieval; no embedding can rank by a number it does not read. Kept as a
   finding for the report ("questions RAG should not answer"). 66 rows -> 65 (dev 40 -> 39, test 26 unchanged;
   the other categories' stratification is untouched, menu_lookup dev 9 -> 8).
3. **Label sets completed by query, not by hand** (die-03, die-09, die-12, and die-11 for consistency). Method: take the
   row's own filters (the `Filters` object and `_item_clause` SQL the retriever uses), add the query's
   intent as a category/spice predicate, list every `menu_items` row that passes, then sanity-check the
   list by eye.
   - die-03 "gluten free main": gluten_free AND category in (Mains, Bowls & Curries) -> 4 items, unchanged.
   - die-09 "spicy but no soy": no soy AND spice_level >= 1 AND not a drink -> 7 items (was 4: added Pad Thai,
     Lamb Kofta Plate, Butternut Squash Soup). Ginger Beer (spice 1) is excluded: a drink, not a dish.
   - die-12 "drink dairy and gluten free": no dairy, no gluten, category Drinks -> 4 items, unchanged
     (includes Pinot Grigio, as a diner asking for "a drink" would accept wine).
   - die-11 "egg allergy, what mains": no egg AND category in (Mains, Bowls & Curries) -> 9 items (was 4). Not on
     the approved list: it used a narrower "mains" than die-03/die-07, so it was brought in line (dev row).
4. **die-05 kept as is** ("halal starters, not soup"); a known limitation: embeddings handle negation poorly,
   Phase 5's LLM applies "not soup".
5. **faq-05 reworded** to "do i have to add a tip or is it already on there" (the old query copied 83% of the
   FAQ's words). It now shares almost no words with "gratuity"; that vocabulary gap is the point.
6. **Recall per label group.** Evidence entries take an optional `group` (and a row may set `item_groups`).
   recall@k = groups with at least one member covered in the top-k / groups; no group = its own group.
   nDCG credits a group once (first covering hit, at that hit's grade) and the ideal DCG uses the group's
   MAX grade, so duplicate alternates cannot inflate the ideal. success@k and MRR are unaffected. Grouped:
   alternates that state the same fact, e.g. the dish-guide and PDF description of one dish, the FAQ and PDF
   opening hours, the FAQ and policy dog rule. The menu-item label is NOT merged with prose passages: the
   structured card and a prose chunk are different evidence, and merging them would make the chunk-size
   ablation blind on menu lookups (the item is always retrievable).

