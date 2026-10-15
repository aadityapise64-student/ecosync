import os
import uuid
import logging
import threading
import time
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
from flask import current_app

logger = logging.getLogger(__name__)

# Standard points awarded per detected item category
POINTS_RATE: Dict[str, int] = {
    'PLASTIC': 10,     # High incentive to divert ocean/landfill plastics
    'METAL': 12,       # High scrap/recycling value
    'GLASS': 8,        # Glass cullet recycling
    'PAPER': 6,        # Clean cellulose paper
    'CARDBOARD': 6,    # Corrugated packing cardboard
    'ORGANIC': 4,      # Compostable waste
    'HAZARDOUS': 15,   # E-waste & batteries safe collection
}
PHOTO_UPLOAD_BONUS = 5  # Bonus points just for uploading & sorting waste photos
_SDK_WORKFLOW_LOCK = threading.Lock()
_WORKFLOW_TIMEOUT = (5, 25)
_WORKFLOW_ATTEMPTS = 3


class RoboflowInferenceError(Exception):
    """Safe, user-displayable Roboflow integration error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RoboflowConfigurationError(RoboflowInferenceError):
    pass

# Visual color palette for bounding box annotations
CLASS_COLORS: Dict[str, Tuple[int, int, int]] = {
    'PLASTIC': (37, 99, 235),      # Blue
    'METAL': (217, 119, 6),        # Amber / Orange
    'GLASS': (8, 145, 178),        # Teal / Cyan
    'PAPER': (16, 185, 129),       # Emerald Green
    'CARDBOARD': (180, 83, 9),     # Brown
    'ORGANIC': (34, 197, 94),      # Green
    'HAZARDOUS': (239, 68, 68),    # Red
}
DEFAULT_BOX_COLOR = (139, 92, 246)  # Purple for unknown


def normalize_class_name(raw_name: str) -> str:
    """Standardize model label name to uppercase canonical class."""
    if not raw_name:
        return 'RECYCLABLE'
    s = raw_name.strip().upper()
    for canonical in POINTS_RATE.keys():
        if canonical in s:
            return canonical
    return s


def calculate_rewards(class_counts: Dict[str, int]) -> Tuple[int, Dict[str, int]]:
    """
    Calculate reward points based on detected item counts and return:
    (total_points, points_breakdown_by_class)
    """
    if not class_counts:
        return 0, {}

    breakdown = {}
    total = PHOTO_UPLOAD_BONUS if class_counts else 0

    for cls_name, count in class_counts.items():
        if count <= 0:
            continue
        rate = POINTS_RATE.get(cls_name.upper(), 5)
        earned = count * rate
        breakdown[cls_name] = earned
        total += earned

    return total, breakdown


def run_roboflow_workflow(image_path: str) -> List[Dict[str, Any]]:
    """Run the configured Roboflow Workflow with bounded retries and timeout."""
    api_key = current_app.config.get('ROBOFLOW_API_KEY', '').strip()
    if not api_key:
        raise RoboflowConfigurationError(
            "Live scanner is not configured. Add ROBOFLOW_API_KEY to the app's .env file or deployment secrets."
        )

    workspace = current_app.config.get('ROBOFLOW_WORKSPACE', 'aaditya-pise').strip()
    workflow_id = current_app.config.get('ROBOFLOW_WORKFLOW_ID', 'general-segmentation-api-7').strip()
    api_url = current_app.config.get('ROBOFLOW_API_URL', 'https://serverless.roboflow.com').rstrip('/')
    classes = current_app.config.get('ROBOFLOW_CLASSES', 'PAPER, PLASTIC, GLASS, METAL, CARDBOARD')

    try:
        from importlib import import_module
        from inference_sdk import InferenceHTTPClient, InferenceConfiguration
        from inference_sdk.http.errors import HTTPCallErrorError, HTTPClientError
        from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout
        sdk_client_module = import_module('inference_sdk.http.client')
    except ImportError as exc:
        raise RoboflowConfigurationError(
            "Roboflow inference SDK is missing. Install the project's requirements before scanning."
        ) from exc

    client = InferenceHTTPClient(api_url=api_url, api_key=api_key).configure(
        InferenceConfiguration(
            api_key_transport='header',
            workflow_run_retries_enabled=False,
        )
    )

    # inference-sdk 1.7.x does not expose a timeout argument on run_workflow.
    # Override only its workflow POST boundary while this serialized call runs.
    def post_with_timeout(url, payload, headers, enable_retries):
        response = sdk_client_module.requests.post(
            url, json=payload, headers=headers, timeout=_WORKFLOW_TIMEOUT
        )
        response.raise_for_status()
        return response

    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
    with _SDK_WORKFLOW_LOCK:
        original_sender = sdk_client_module.send_post_request
        sdk_client_module.send_post_request = post_with_timeout
        try:
            for attempt in range(_WORKFLOW_ATTEMPTS):
                try:
                    result = client.run_workflow(
                        workspace_name=workspace,
                        workflow_id=workflow_id,
                        images={'image': image_path},
                        parameters={'classes': classes},
                        use_cache=True,
                    )
                    if not isinstance(result, list):
                        raise RoboflowInferenceError(
                            "Roboflow returned an unexpected workflow response."
                        )
                    return result
                except HTTPCallErrorError as exc:
                    status_code = getattr(exc, 'status_code', None)
                    if status_code not in retryable_statuses:
                        if status_code in (401, 403):
                            message = "Roboflow rejected the API key. Check that the rotated key belongs to this workspace."
                        elif status_code == 404:
                            message = "Roboflow could not find the configured workflow. Verify its workspace and workflow slug."
                        elif status_code == 413:
                            message = "The image is too large for Roboflow inference. Upload a smaller image."
                        else:
                            message = "Roboflow rejected the scan request. Check the workflow configuration."
                        raise RoboflowInferenceError(message, status_code) from exc
                    error = exc
                except (HTTPClientError, RequestsConnectionError, RequestsTimeout) as exc:
                    error = exc
                except RoboflowInferenceError:
                    raise
                except Exception as exc:
                    raise RoboflowInferenceError(
                        "The Roboflow workflow could not complete the scan. Check the server logs."
                    ) from exc

                if attempt + 1 == _WORKFLOW_ATTEMPTS:
                    raise RoboflowInferenceError(
                        "Roboflow is temporarily unavailable. Please try again shortly."
                    ) from error
                time.sleep(0.5 * (2 ** attempt))
        finally:
            sdk_client_module.send_post_request = original_sender

    raise RoboflowInferenceError("Roboflow is temporarily unavailable.")


def extract_predictions_from_response(resp_data: Any) -> List[Dict[str, Any]]:
    """
    Flexibly extracts list of detected predictions from various Roboflow workflow output formats.
    """
    predictions: List[Dict[str, Any]] = []

    prediction_fields = {'class', 'class_name', 'label', 'name'}
    if isinstance(resp_data, dict):
        if prediction_fields.intersection(resp_data):
            return [resp_data]
        for value in resp_data.values():
            predictions.extend(extract_predictions_from_response(value))
    elif isinstance(resp_data, list):
        for item in resp_data:
            if isinstance(item, dict) and prediction_fields.intersection(item):
                predictions.append(item)
            else:
                predictions.extend(extract_predictions_from_response(item))

    return predictions


def _contains_collection(value: Any) -> bool:
    """Return whether a workflow response contains a list-shaped output."""
    if isinstance(value, list):
        return True
    if isinstance(value, dict):
        return any(_contains_collection(child) for child in value.values())
    return False


def draw_bounding_boxes(
    image_path: str,
    predictions: List[Dict[str, Any]],
    output_filename: Optional[str] = None
) -> Optional[str]:
    """
    Annotate image with detected items' bounding boxes and category labels.
    Saves and returns the output filename inside the configured upload folder.
    """
    try:
        if not os.path.exists(image_path):
            return None

        with Image.open(image_path) as im:
            # Convert to RGB in case of RGBA / palette mode
            img = im.convert('RGB')
            draw = ImageDraw.Draw(img)
            w, h = img.size

            font = None
            try:
                # Try default truetype font if available on Windows
                font = ImageFont.truetype("arial.ttf", size=max(14, int(w * 0.02)))
            except Exception:
                font = ImageFont.load_default()

            for pred in predictions:
                raw_class = pred.get('class') or pred.get('class_name') or pred.get('label') or 'Item'
                cls_name = normalize_class_name(str(raw_class))
                confidence = float(pred.get('confidence', 0.0))
                color = CLASS_COLORS.get(cls_name, DEFAULT_BOX_COLOR)

                # Roboflow center format (x, y, width, height)
                x = pred.get('x')
                y = pred.get('y')
                bw = pred.get('width')
                bh = pred.get('height')

                if x is not None and y is not None and bw is not None and bh is not None:
                    # Center format to corner coords
                    x1 = max(0, int(x - bw / 2))
                    y1 = max(0, int(y - bh / 2))
                    x2 = min(w, int(x + bw / 2))
                    y2 = min(h, int(y + bh / 2))
                elif 'box' in pred and isinstance(pred['box'], (list, tuple)) and len(pred['box']) >= 4:
                    x1, y1, x2, y2 = [int(v) for v in pred['box'][:4]]
                else:
                    continue

                # Draw rectangle with border thickness
                line_width = max(3, int(w * 0.005))
                draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)

                # Draw label banner
                conf_pct = f"{int(confidence * 100)}%" if confidence > 0 else ""
                label_text = f" {cls_name} {conf_pct} ".strip()

                text_bbox = draw.textbbox((x1, y1), label_text, font=font)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]

                badge_y1 = max(0, y1 - text_h - 6)
                badge_y2 = y1
                draw.rectangle([x1, badge_y1, x1 + text_w + 10, badge_y2], fill=color)
                draw.text((x1 + 5, badge_y1 + 2), label_text, fill=(255, 255, 255), font=font)

            if not output_filename:
                output_filename = f"annotated_{uuid.uuid4().hex[:12]}.jpg"

            upload_folder = current_app.config['UPLOAD_FOLDER']
            os.makedirs(upload_folder, exist_ok=True)
            output_path = os.path.join(upload_folder, output_filename)
            img.save(output_path, 'JPEG', quality=90)
            return output_filename

    except Exception as e:
        logger.warning(f"Failed to annotate image: {e}")
        return None


def run_simulated_detection(
    image_path: str,
    save_annotated: bool = True
) -> Dict[str, Any]:
    """
    Smart demo / fallback detector used when Roboflow API key is not yet configured.
    Generates realistic detection data and draws bounding boxes so that the user
    can test the complete reward workflow immediately.
    """
    width, height = 640, 480
    if os.path.exists(image_path):
        try:
            with Image.open(image_path) as im:
                width, height = im.size
        except Exception:
            pass

    # Simulated realistic recyclable items
    mock_predictions = [
        {
            'class': 'PLASTIC',
            'confidence': 0.94,
            'x': width * 0.35,
            'y': height * 0.45,
            'width': width * 0.28,
            'height': height * 0.40
        },
        {
            'class': 'PLASTIC',
            'confidence': 0.88,
            'x': width * 0.65,
            'y': height * 0.50,
            'width': width * 0.22,
            'height': height * 0.35
        },
        {
            'class': 'GLASS',
            'confidence': 0.91,
            'x': width * 0.50,
            'y': height * 0.30,
            'width': width * 0.20,
            'height': height * 0.32
        },
        {
            'class': 'METAL',
            'confidence': 0.85,
            'x': width * 0.20,
            'y': height * 0.70,
            'width': width * 0.18,
            'height': height * 0.22
        }
    ]

    class_counts = {'PLASTIC': 2, 'GLASS': 1, 'METAL': 1}
    total_items = sum(class_counts.values())
    total_points, points_breakdown = calculate_rewards(class_counts)

    annotated_filename = None
    if save_annotated and os.path.exists(image_path):
        annotated_filename = draw_bounding_boxes(image_path, mock_predictions)

    return {
        'success': True,
        'is_simulated': True,
        'can_claim_rewards': False,
        'message': "Demo detection (configure ROBOFLOW_API_KEY in .env for live cloud inference)",
        'total_items': total_items,
        'class_counts': class_counts,
        'points_awarded': total_points,
        'points_breakdown': points_breakdown,
        'predictions': mock_predictions,
        'annotated_image': annotated_filename,
        'recommended_category': 'Recyclable',
        'summary': f"Detected {total_items} recyclable items: 2 Plastic, 1 Glass, 1 Metal. Reward: +{total_points} pts!"
    }


def detect_trash_from_file(
    image_path: str,
    save_annotated: bool = True
) -> Dict[str, Any]:
    """
    Main function to run Roboflow trash detection and segmentation on an image file.
    Connects to the serverless Roboflow workflow API:
    https://serverless.roboflow.com/{workspace_name}/workflows/{workflow_id}
    """
    if not os.path.exists(image_path):
        return {
            'success': False, 'is_simulated': False, 'can_claim_rewards': False,
            'message': "The uploaded image could not be found.", 'total_items': 0,
            'class_counts': {}, 'points_awarded': 0, 'points_breakdown': {},
            'predictions': [], 'annotated_image': None,
            'recommended_category': 'Recyclable', 'summary': "Scan unavailable."
        }

    # Never fabricate predictions when live credentials are missing. Demo mode
    # must be explicitly enabled for local development only.
    if not current_app.config.get('ROBOFLOW_API_KEY', '').strip():
        if current_app.config.get('AI_DEMO_MODE', False):
            logger.info("Roboflow is not configured; explicit AI demo mode is enabled.")
            return run_simulated_detection(image_path, save_annotated=save_annotated)
        logger.warning("ROBOFLOW_API_KEY is not configured; live scanner is unavailable.")
        return {
            'success': False, 'is_simulated': False, 'can_claim_rewards': False,
            'message': "Live scanner is not configured. Add ROBOFLOW_API_KEY to the app's .env file or deployment secrets.",
            'total_items': 0, 'class_counts': {}, 'points_awarded': 0,
            'points_breakdown': {}, 'predictions': [], 'annotated_image': None,
            'recommended_category': 'Recyclable', 'summary': "Scan unavailable."
        }

    try:
        data = run_roboflow_workflow(image_path)

        # A one-image Workflow run returns one dictionary of named outputs.
        # Keep output names dynamic, but reject responses that do not resemble
        # the SDK's documented one-result-per-image structure.
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise RoboflowInferenceError(
                "Roboflow returned an unexpected workflow response."
            )

        # 5. Extract predictions
        predictions = extract_predictions_from_response(data)
        has_collection_output = any(_contains_collection(entry) for entry in data)
        # Workflow image outputs are not used by this scanner; release them
        # before rendering or persisting the compact detection records.
        del data

        # Count detected items per category
        class_counts: Dict[str, int] = {}
        valid_predictions = []

        for p in predictions:
            # Filter low confidence if present
            conf = float(p.get('confidence', 1.0))
            if conf < 0.25:
                continue
            raw_cls = p.get('class') or p.get('class_name') or p.get('label') or 'RECYCLABLE'
            norm_cls = normalize_class_name(str(raw_cls))
            if norm_cls not in POINTS_RATE:
                continue
            # Keep only the fields the UI uses; never persist polygon masks or
            # other large workflow payloads.
            valid_predictions.append({
                key: p[key]
                for key in ('class', 'class_name', 'label', 'confidence', 'x', 'y', 'width', 'height', 'box')
                if key in p
            })
            class_counts[norm_cls] = class_counts.get(norm_cls, 0) + 1

        total_items = len(valid_predictions)

        # Empty prediction lists are valid live inference results. An actual
        # list-shaped workflow output distinguishes zero detections from a
        # malformed scalar-only response without depending on output names.
        if not predictions and not has_collection_output:
            logger.warning("Roboflow returned a response without a recognized collection output.")
            return {
                'success': False, 'is_simulated': False, 'can_claim_rewards': False,
                'message': "Roboflow returned an unexpected workflow output. Check the Workflow output and model labels.",
                'total_items': 0, 'class_counts': {}, 'points_awarded': 0,
                'points_breakdown': {}, 'predictions': [], 'annotated_image': None,
                'recommended_category': 'Recyclable', 'summary': "Scan unavailable."
            }

        annotated_filename = None
        if save_annotated and valid_predictions:
            annotated_filename = draw_bounding_boxes(image_path, valid_predictions)

        # 6. Calculate Reward Points
        total_points, points_breakdown = calculate_rewards(class_counts)

        # Summary text
        if total_items > 0:
            breakdown_str = ", ".join([f"{count} {cls.capitalize()}" for cls, count in class_counts.items()])
            summary = f"AI detected {total_items} items ({breakdown_str}). Earned {total_points} EcoPoints!"
        else:
            summary = "Live scan complete. No supported waste items were detected; no points were awarded."

        return {
            'success': True,
            'is_simulated': False,
            'can_claim_rewards': total_points > 0,
            'message': "Live Roboflow model inference complete.",
            'total_items': total_items,
            'class_counts': class_counts,
            'points_awarded': total_points,
            'points_breakdown': points_breakdown,
            'predictions': valid_predictions,
            'annotated_image': annotated_filename,
            'recommended_category': (
                'Recyclable' if any(c in class_counts for c in ['PLASTIC', 'GLASS', 'METAL', 'PAPER', 'CARDBOARD'])
                else 'Organic' if 'ORGANIC' in class_counts
                else None
            ),
            'summary': summary
        }

    except RoboflowInferenceError as exc:
        logger.warning("Roboflow inference failed (status=%s).", exc.status_code)
        return {
            'success': False, 'is_simulated': False, 'can_claim_rewards': False,
            'message': str(exc),
            'total_items': 0, 'class_counts': {}, 'points_awarded': 0,
            'points_breakdown': {}, 'predictions': [], 'annotated_image': None,
            'recommended_category': 'Recyclable', 'summary': "Scan unavailable."
        }
    except Exception as exc:
        logger.error("Roboflow inference failed unexpectedly (%s).", type(exc).__name__)
        return {
            'success': False, 'is_simulated': False, 'can_claim_rewards': False,
            'message': "The scanner could not process this image. Check the app logs and try again.",
            'total_items': 0, 'class_counts': {}, 'points_awarded': 0,
            'points_breakdown': {}, 'predictions': [], 'annotated_image': None,
            'recommended_category': 'Recyclable', 'summary': "Scan unavailable."
        }
