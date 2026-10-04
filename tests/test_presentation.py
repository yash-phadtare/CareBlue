"""Hospital color customization must retain readable controls without changing stored branding."""
import unittest
from careblue.presentation import brand_palette, luminance


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


class BrandPaletteTests(unittest.TestCase):
    def test_white_control_labels_remain_readable_for_custom_brand_colors(self):
        for color in ('#FFFFFF', '#FFFF00', '#00FF00', '#FF0000', '#1677FF', '#7431AD', '#000000'):
            with self.subTest(color=color):
                palette = brand_palette(color)
                self.assertEqual(palette['brand'], color)
                for state in ('primary', 'dark'):
                    self.assertGreaterEqual(1.05 / (luminance(rgb(palette[state])) + .05), 5.5)

    def test_tonal_controls_and_active_navigation_remain_readable(self):
        for red in range(0, 256, 51):
            for green in range(0, 256, 51):
                for blue in range(0, 256, 51):
                    palette = brand_palette(f'#{red:02x}{green:02x}{blue:02x}')
                    contrast = (luminance(rgb(palette['soft'])) + .05) / (luminance(rgb(palette['dark'])) + .05)
                    self.assertGreaterEqual(contrast, 4.5)

    def test_malformed_colors_fall_back_to_safe_brand(self):
        for color in (None, '', 'red', '#fff', '#fff; color:red', '<script>'):
            self.assertEqual(brand_palette(color), brand_palette('#1677FF'))

    def test_dark_hospital_actions_and_original_brand_labels_are_readable(self):
        for red in range(0, 256, 51):
            for green in range(0, 256, 51):
                for blue in range(0, 256, 51):
                    palette = brand_palette(f'#{red:02x}{green:02x}{blue:02x}')
                    self.assertGreaterEqual((luminance(rgb(palette['night_primary']))+.05)/(luminance(rgb('#1c1b1f'))+.05),4.5)
                    self.assertGreaterEqual((luminance(rgb(palette['night_dark']))+.05)/(luminance(rgb(palette['night_soft']))+.05),4.5)
                    luminances=sorted([luminance(rgb(palette['brand'])),luminance(rgb(palette['on_brand']))])
                    self.assertGreaterEqual((luminances[1]+.05)/(luminances[0]+.05),4.5)
