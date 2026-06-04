"""Smoke test for the sklearn-based categorizer.

Purpose: detect when sklearn / scipy / numpy submodules required by the
RandomForest + TF-IDF pipeline are missing or broken — including in
PyInstaller bundles where modules can be excluded at packaging time.

The dev-env pytest run exercises this against the full venv (and thus
won't catch bundle pruning issues by itself). The same logic is also
invoked via `desktop/launcher.py --smoke-test` against the built
binary, which is where bundle exclusions actually matter.
"""

from app.services.categorizer import (
    categorize_transaction,
    train_classifier,
)


TRAINING_DATA = [
    ("STARBUCKS COFFEE STORE 1234", "Expenses:Food:Coffee"),
    ("STARBUCKS RESERVE ROASTERY", "Expenses:Food:Coffee"),
    ("BLUE BOTTLE COFFEE", "Expenses:Food:Coffee"),
    ("PEETS COFFEE", "Expenses:Food:Coffee"),
    ("PHILZ COFFEE", "Expenses:Food:Coffee"),
    ("SHELL GAS STATION", "Expenses:Auto:Fuel"),
    ("CHEVRON GAS", "Expenses:Auto:Fuel"),
    ("EXXON MOBIL", "Expenses:Auto:Fuel"),
    ("BP STATION", "Expenses:Auto:Fuel"),
    ("ARCO GAS", "Expenses:Auto:Fuel"),
    ("WHOLE FOODS MARKET", "Expenses:Food:Groceries"),
    ("TRADER JOES", "Expenses:Food:Groceries"),
    ("SAFEWAY", "Expenses:Food:Groceries"),
    ("KROGER STORE", "Expenses:Food:Groceries"),
    ("COSTCO WHOLESALE", "Expenses:Food:Groceries"),
]


def test_categorizer_trains_and_predicts():
    """End-to-end smoke: import sklearn, train pipeline, predict a known category.

    Asserts:
      - the sklearn import path resolves (TfidfVectorizer, RandomForest, Pipeline);
      - the pipeline fits without error;
      - prediction returns a category from the training set;
      - confidence is in [0.0, 1.0].

    Does NOT assert a specific predicted label — small datasets + RF
    nondeterminism across platforms can shift winners. The point of this
    test is to catch missing modules, not regress model accuracy.
    """
    pipeline = train_classifier(TRAINING_DATA)
    assert pipeline is not None, "train_classifier returned None"

    known_categories = {cat for _, cat in TRAINING_DATA}

    category, confidence = categorize_transaction(
        "STARBUCKS COFFEE NEW STORE", pipeline
    )
    assert category in known_categories, (
        f"Prediction {category!r} not in training categories"
    )
    assert 0.0 <= confidence <= 1.0, f"Confidence {confidence} out of range"


def test_categorizer_predicts_multiple_categories():
    """Sanity check: pipeline can distinguish between training categories.

    With 5 examples per category and reasonably distinct vocabularies,
    a working RF+TFIDF pipeline should match each query to its own
    category at least most of the time. We assert it gets at least 2
    out of 3 queries right — loose enough to avoid CPU/platform flakes,
    strict enough to catch a pipeline that's silently returning garbage.
    """
    pipeline = train_classifier(TRAINING_DATA)
    queries = [
        ("BLUE BOTTLE COFFEE DOWNTOWN", "Expenses:Food:Coffee"),
        ("SHELL FUEL", "Expenses:Auto:Fuel"),
        ("TRADER JOES NEIGHBORHOOD", "Expenses:Food:Groceries"),
    ]
    correct = sum(
        1
        for query, expected in queries
        if categorize_transaction(query, pipeline)[0] == expected
    )
    assert correct >= 2, f"Pipeline only got {correct}/3 obvious queries right"
