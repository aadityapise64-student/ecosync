import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from services.ai_detector import detect_trash_from_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_IMAGE = ROOT / 'static' / 'uploads' / 'waste_03ef4b26673f.jpg'


def workflow_response(predictions):
    """Mirror the live workflow's nested prediction response without image blobs."""
    return [{
        'predictions': {
            'image': {'width': 640, 'height': 480},
            'predictions': predictions,
        },
        'annotated_image': '[omitted image output]',
    }]


class PhaseOneScannerTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY='phase-one-test-only',
            ROBOFLOW_API_KEY='test-key-not-used-by-mocked-tests',
            AI_DEMO_MODE=False,
            UPLOAD_FOLDER=str(ROOT / 'tmp' / 'phase-one-test-uploads'),
        )
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.assertTrue(SAMPLE_IMAGE.is_file(), f'Missing fixture image: {SAMPLE_IMAGE}')

    @patch('services.ai_detector.run_roboflow_workflow')
    def test_positive_live_result_counts_and_rewards(self, run_workflow):
        run_workflow.return_value = workflow_response([
            {'class': 'PLASTIC', 'confidence': 0.92, 'x': 120, 'y': 130, 'width': 60, 'height': 80},
            {'class': 'PLASTIC', 'confidence': 0.88, 'x': 220, 'y': 230, 'width': 60, 'height': 80},
        ])

        result = detect_trash_from_file(str(SAMPLE_IMAGE), save_annotated=False)

        self.assertTrue(result['success'])
        self.assertFalse(result['is_simulated'])
        self.assertEqual(result['total_items'], 2)
        self.assertEqual(result['class_counts'], {'PLASTIC': 2})
        self.assertEqual(result['points_breakdown'], {'PLASTIC': 20})
        self.assertEqual(result['points_awarded'], 25)
        self.assertTrue(result['can_claim_rewards'])

    @patch('services.ai_detector.run_roboflow_workflow')
    def test_valid_empty_live_result_is_zero_points_not_failure(self, run_workflow):
        run_workflow.return_value = workflow_response([])

        result = detect_trash_from_file(str(SAMPLE_IMAGE), save_annotated=False)

        self.assertTrue(result['success'])
        self.assertFalse(result['is_simulated'])
        self.assertEqual(result['total_items'], 0)
        self.assertEqual(result['class_counts'], {})
        self.assertEqual(result['points_breakdown'], {})
        self.assertEqual(result['points_awarded'], 0)
        self.assertFalse(result['can_claim_rewards'])
        self.assertIn('no points were awarded', result['summary'].lower())

    @patch('services.ai_detector.run_roboflow_workflow')
    def test_malformed_scalar_only_result_is_unavailable(self, run_workflow):
        run_workflow.return_value = [{'status': 'done'}]

        result = detect_trash_from_file(str(SAMPLE_IMAGE), save_annotated=False)

        self.assertFalse(result['success'])
        self.assertFalse(result['is_simulated'])
        self.assertEqual(result['points_awarded'], 0)
        self.assertFalse(result['can_claim_rewards'])

    @patch('services.ai_detector.run_roboflow_workflow')
    def test_unsupported_model_label_does_not_award_points(self, run_workflow):
        run_workflow.return_value = workflow_response([
            {'class': 'UNKNOWN_MATERIAL', 'confidence': 0.99},
        ])

        result = detect_trash_from_file(str(SAMPLE_IMAGE), save_annotated=False)

        self.assertTrue(result['success'])
        self.assertEqual(result['class_counts'], {})
        self.assertEqual(result['points_awarded'], 0)
        self.assertFalse(result['can_claim_rewards'])


if __name__ == '__main__':
    unittest.main()
