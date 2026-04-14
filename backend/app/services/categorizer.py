"""
ML-based transaction categorization service.

This module implements stateless ML categorization using Random Forest with TF-IDF.
Uses pre-computed training data from LedgerCache to avoid redundant ledger parsing.
"""

import logging
import re
from typing import List, Tuple, Optional

from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)


class CategorizerError(Exception):
    """Exception raised when categorization operations fail."""
    pass


class TrainingDataError(Exception):
    """Exception raised when training data is insufficient or invalid."""
    pass


def preprocess_text(text: str) -> str:
    """
    Preprocess transaction description text for ML training.

    Based on specification requirements:
    - Remove all special characters (replace with spaces)
    - Remove all words containing numbers
    - Remove all single characters
    - Substitute multiple spaces with single space
    - Convert to lowercase
    - Remove specific terms: "aplpay", "com"

    Args:
        text: Raw description text (payee + memo)

    Returns:
        Preprocessed text ready for vectorization
    """
    if not text:
        return ""

    # Convert to lowercase
    text = text.lower()

    # Remove specific terms
    text = text.replace("aplpay", " ").replace("com", " ")

    # Replace all special characters with spaces
    text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)

    # Split into words
    words = text.split()

    # Remove words containing numbers and single characters
    filtered_words = []
    for word in words:
        if not re.search(r'\d', word) and len(word) > 1:
            filtered_words.append(word)

    # Join back and normalize spaces
    result = ' '.join(filtered_words)
    result = re.sub(r'\s+', ' ', result).strip()

    return result


def train_classifier(training_data: List[Tuple[str, str]]) -> Optional[Pipeline]:
    """
    Train a Random Forest classifier on transaction data.

    Args:
        training_data: List of (description, category) tuples

    Returns:
        Trained sklearn Pipeline or None if insufficient data

    Raises:
        TrainingDataError: If training data is insufficient or invalid
    """
    if not training_data:
        raise TrainingDataError("No training data provided")

    if len(training_data) < 10:
        raise TrainingDataError(
            f"Insufficient training data: {len(training_data)} transactions (minimum: 10)"
        )

    # Prepare features and labels
    features = []
    labels = []

    for description, category in training_data:
        processed_description = preprocess_text(description)

        if not processed_description:
            continue  # Skip empty descriptions

        features.append(processed_description)
        labels.append(category)

    if len(features) < 5:
        raise TrainingDataError(f"Insufficient valid training features: {len(features)}")

    # Check for minimum category diversity
    unique_categories = set(labels)
    if len(unique_categories) < 2:
        raise TrainingDataError("Training data must have at least 2 different categories")

    try:
        # Create pipeline with TF-IDF and Random Forest
        pipeline = Pipeline([
            ('tfidf', TfidfVectorizer()),
            ('classifier', RandomForestClassifier(
                n_estimators=100,
                random_state=42,
                n_jobs=-1  # Use all CPU cores
            ))
        ])

        # Train the model
        pipeline.fit(features, labels)

        logger.info(f"Classifier trained successfully with {len(features)} samples and {len(unique_categories)} categories")
        return pipeline

    except Exception as e:
        raise CategorizerError(f"Failed to train classifier: {e}")


def categorize_transaction(text: str, classifier: Pipeline, default_account: str = "Expenses:Unknown") -> Tuple[str, float]:
    """
    Predict category for a transaction using trained classifier.

    Args:
        text: Transaction description (payee + memo)
        classifier: Trained sklearn Pipeline
        default_account: Account to use when prediction is not possible

    Returns:
        Tuple of (predicted_category, confidence_score)
    """
    if not classifier:
        raise CategorizerError("Classifier is None")

    # Preprocess text
    processed_text = preprocess_text(text)

    if not processed_text:
        # Return default with zero confidence for empty text
        return default_account, 0.0

    try:
        # Predict category
        prediction = classifier.predict([processed_text])[0]
        predicted_category = str(prediction)  # Convert numpy result to string

        # Get confidence (probability of predicted class)
        probabilities = classifier.predict_proba([processed_text])[0]
        confidence = float(max(probabilities))

        return predicted_category, confidence

    except Exception as e:
        logger.error(f"Categorization failed: {e}")
        return default_account, 0.0


def initialize_classifier(training_data: List[Tuple[str, str]], ml_enabled: bool) -> Tuple[Optional[Pipeline], Optional[str]]:
    """
    Initialize ML classifier or return None with warning message.

    This function encapsulates the logic for ML initialization, including
    graceful degradation when ML is disabled or training fails.

    Args:
        training_data: Pre-computed training data from LedgerCache
        ml_enabled: Whether ML is enabled in config

    Returns:
        Tuple of (classifier, warning_message)
        - classifier: Trained Pipeline or None
        - warning_message: String explaining why ML is unavailable, or None
    """
    if not ml_enabled:
        return None, "ML is disabled in configuration"

    try:
        # Train classifier with pre-computed data
        classifier = train_classifier(training_data)
        return classifier, None

    except TrainingDataError as e:
        warning_msg = f"ML training skipped: {str(e)}"
        logger.warning(warning_msg)
        return None, warning_msg

    except Exception as e:
        warning_msg = f"ML training failed: {str(e)}"
        logger.error(warning_msg)
        return None, warning_msg
