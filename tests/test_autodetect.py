import unittest

import numpy as np

import synth
import autodetect as ad

CW, CH = 1400, 800


def search_region(client):
    det = ad.SubtitleDetector()
    x0, y0, w, h = det.search_rect(CW, CH)
    return det, client[y0:y0 + h, x0:x0 + w], (x0, y0)


class ReferenceTests(unittest.TestCase):
    def test_reference_loads(self):
        ref = ad.load_reference()
        self.assertEqual(ref.ndim, 2)
        self.assertGreater(ref.shape[1], ref.shape[0] * 5)   # a wide line of text
        self.assertTrue(set(np.unique(ref)) <= {30, 220})    # clean two-level image

    def test_reference_at_scale(self):
        det = ad.SubtitleDetector()
        big = det.reference_at(4)
        self.assertEqual(big.shape, (det.ref.shape[0] * 4, det.ref.shape[1] * 4))


class ScaleDetectionTests(unittest.TestCase):
    def test_finds_the_right_scale_and_position(self):
        for scale in range(1, 7):
            with self.subTest(scale=scale):
                client, (tx, ty) = synth.make_client(scale, CW, CH)
                det, region, origin = search_region(client)
                found, score, loc = det.best_scale(region, origin, det.candidate_scales(CW, CH))
                self.assertEqual(found, scale)
                self.assertGreater(score, 0.95)
                self.assertEqual(loc, (tx - origin[0], ty - origin[1]))

    def test_known_scale_check_is_high_and_wrong_scales_are_low(self):
        client, _ = synth.make_client(5, CW, CH)
        det, region, origin = search_region(client)
        self.assertGreater(det.score(region, origin, 5)[0], 0.95)
        for wrong in (1, 2, 3, 4, 6, 7):
            with self.subTest(wrong=wrong):
                self.assertLess(det.score(region, origin, wrong)[0], 0.7)

    def test_faded_text_still_matches(self):
        # Subtitles fade as they age; matching must not depend on brightness.
        for fade in (1.0, 0.7, 0.5, 0.35):
            with self.subTest(fade=fade):
                client, _ = synth.make_client(4, CW, CH, fade=fade)
                det, region, origin = search_region(client)
                found, score, _ = det.best_scale(region, origin, det.candidate_scales(CW, CH))
                self.assertEqual(found, 4)
                self.assertGreaterEqual(score, ad.LEARN_THRESHOLD)

    def test_different_backgrounds(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                client, _ = synth.make_client(3, CW, CH, seed=seed)
                det, region, origin = search_region(client)
                found, score, _ = det.best_scale(region, origin, det.candidate_scales(CW, CH))
                self.assertEqual(found, 3)
                self.assertGreaterEqual(score, ad.LEARN_THRESHOLD)

    def test_origin_that_is_not_on_the_grid(self):
        # The crop starts at an arbitrary pixel; the detector must realign to the block grid itself.
        for scale in (2, 3, 5):
            with self.subTest(scale=scale):
                client, _ = synth.make_client(scale, CW, CH)
                det = ad.SubtitleDetector()
                x0, y0 = 617, 307          # deliberately not a multiple of anything useful
                region = client[y0:, x0:]
                found, score, _ = det.best_scale(region, (x0, y0), range(1, 7))
                self.assertEqual(found, scale)
                self.assertGreater(score, 0.95)


class NegativeTests(unittest.TestCase):
    def test_no_subtitle_never_reaches_the_learn_threshold(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                client, _ = synth.make_client(4, CW, CH, seed=seed, draw_text=False)
                det, region, origin = search_region(client)
                _, score, _ = det.best_scale(region, origin, det.candidate_scales(CW, CH))
                self.assertLess(score, ad.LEARN_THRESHOLD)

    def test_different_text_is_rejected(self):
        # Same box, same font size, but the glyph pattern is scrambled: not "Fishing Bobber splashes".
        ref = ad.load_reference()
        scrambled = np.ascontiguousarray(np.roll(ref, ref.shape[1] // 3, axis=1)[::-1])
        client, _ = synth.make_client(4, CW, CH, reference=scrambled)
        det, region, origin = search_region(client)
        _, score, _ = det.best_scale(region, origin, det.candidate_scales(CW, CH))
        self.assertLess(score, ad.LEARN_THRESHOLD)

    def test_flat_region_does_not_crash(self):
        det = ad.SubtitleDetector()
        flat = np.full((300, 600, 3), 17, dtype=np.uint8)
        self.assertEqual(det.best_scale(flat, (0, 0), range(1, 5))[1], 0.0)

    def test_region_smaller_than_reference(self):
        det = ad.SubtitleDetector()
        tiny = np.zeros((5, 5, 3), dtype=np.uint8)
        self.assertEqual(det.score(tiny, (0, 0), 1), (0.0, None))


class GeometryTests(unittest.TestCase):
    def test_search_rect_is_the_bottom_right_and_inside_the_window(self):
        det = ad.SubtitleDetector()
        x, y, w, h = det.search_rect(2560, 1351)
        self.assertEqual((x + w, y + h), (2560, 1351))
        self.assertGreater(x, 2560 * 0.4)
        self.assertGreater(y, 1351 * 0.3)

    def test_candidate_scales_include_realistic_ones(self):
        det = ad.SubtitleDetector()
        scales = det.candidate_scales(2560, 1351)
        for s in (1, 2, 3, 4, 5):
            self.assertIn(s, scales)

    def test_tiny_window_has_few_candidates(self):
        self.assertEqual(ad.SubtitleDetector().candidate_scales(200, 100), [])


if __name__ == "__main__":
    unittest.main()
