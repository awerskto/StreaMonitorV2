"""Unit tests of the scrub storyboard mapping.

The mapping of the position to the frame index and of the frame index to
the background position are pure functions, so they are tested with node
and without a browser. The edges are where it breaks.
"""
import os
import shutil
import subprocess
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(APP_ROOT, 'streamonitor/managers/httpmanager/static/streamonitor@tube')
SCRUB = os.path.join(STATIC, 'js', 'scrub.js')


def run_mapping(script):
    """Import the scrub module in node and evaluate a snippet."""
    result = subprocess.run(
        ['node', '--input-type=module', '-e',
         f"import {{ frameIndex, cellPosition, clock }} from '{SCRUB}';\n"
         f"const out = (function() {{ {script} }})();\n"
         "console.log('RESULT:' + JSON.stringify(out));"],
        capture_output=True, text=True, timeout=60)
    for line in result.stdout.splitlines():
        if line.startswith('RESULT:'):
            import json
            return json.loads(line[len('RESULT:'):])
    raise AssertionError(f'node failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}')


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_first_frame_is_at_the_left_edge():
    assert run_mapping("return frameIndex(0, 320, 20)") == 0


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_last_frame_is_at_the_right_edge():
    """The right edge is not the frame after the last one."""
    assert run_mapping("return frameIndex(320, 320, 20)") == 19
    assert run_mapping("return frameIndex(319.99, 320, 20)") == 19


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_middle_is_the_middle_frame():
    assert run_mapping("return frameIndex(160, 320, 20)") == 10


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_quarters_map_to_the_quarters_of_the_storyboard():
    assert run_mapping("return frameIndex(80, 320, 20)") == 5
    assert run_mapping("return frameIndex(240, 320, 20)") == 15


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_every_boundary_lands_on_the_next_frame_without_a_gap():
    """The twenty intervals cover the width exactly once."""
    out = run_mapping("""
        const seen = [];
        for (let x = 0; x <= 320; x += 1) seen.push(frameIndex(x, 320, 20));
        return { seen, unique: [...new Set(seen)] };
    """)
    assert out['unique'] == list(range(20)), sorted(set(out['unique']))
    # The mapping only moves forward.
    assert out['seen'] == sorted(out['seen'])


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_a_degenerate_width_and_frame_count_do_not_break():
    assert run_mapping("return [frameIndex(10, 0, 20), frameIndex(10, 320, 0)]") == [0, 0]
    assert run_mapping("return frameIndex(NaN, 320, 20)") == 0


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_first_cell_starts_at_zero_percent():
    assert run_mapping("return cellPosition(0, 5, 4)") == {'x': 0, 'y': 0}


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_last_column_ends_at_hundred_percent():
    """Without this the last column of the sprite would be cut off."""
    assert run_mapping("return cellPosition(4, 5, 4)") == {'x': 100, 'y': 0}
    assert run_mapping("return cellPosition(19, 5, 4)") == {'x': 100, 'y': 100}


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_middle_cells_are_evenly_spaced():
    """Index 2 is the third column, index 10 the first column of row 3."""
    out = run_mapping("return [cellPosition(2, 5, 4), cellPosition(10, 5, 4),"
                      " cellPosition(12, 5, 4)]")
    assert out[0] == {'x': 50, 'y': 0}
    assert out[1] == {'x': 0, 'y': 66.66666666666666}
    assert out[2] == {'x': 50, 'y': 66.66666666666666}


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_a_single_row_or_column_is_centred():
    """One row: the vertical position must not divide by zero."""
    assert run_mapping("return cellPosition(0, 5, 1)") == {'x': 0, 'y': 50}


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_clock_reads_the_interval_of_a_frame():
    assert run_mapping("return clock(0)") == '00:00'
    assert run_mapping("return clock(3725)") == '1:02:05'
    assert run_mapping("return [clock(null), clock(NaN)]") == ['--:--', '--:--']
