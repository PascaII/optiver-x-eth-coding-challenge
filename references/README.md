# Reference projects

These are unmodified source snapshots, including upstream documentation, tests,
and MIT licenses. They have no nested Git repositories or submodules, so they
travel with a normal clone of this repository. They are not installed as project
dependencies. Snapshots were retrieved on 2026-11-05.

| Directory | Source | Branch | Commit |
| --- | --- | --- | --- |
| `beatzxbt/smm` | https://github.com/beatzxbt/smm | `master` | `a9799babd88a62030097028e943f36013b5ba505` |
| `beatzxbt/mm-toolbox` | https://github.com/beatzxbt/mm-toolbox | `master` | `201b01ca9d63f23b096355e20cb1e6126eda1c0e` |

## Branch choices

### smm

`master` is the default branch and contains the smaller Bybit implementation,
with strategy code under `src/strategy/`. It is easier to study during a short
competition than `v2.0a`, which replaces much of the application with a larger
multi-exchange framework and is described by the master README as work in progress.

Also inspected `fix/feature-math-and-typos` at
`6bcd5f17f779007dbb3f68ac01b9c2ea3467e117`. This unmerged branch corrects
`log_price_difference` to use `log(follow / base)` and basis-point conversion
in `orderbook_imbalance` to multiply depths by `1e-4`. The snapshot preserves
master unchanged; check those differences before adapting either calculation.

Start with:
- `beatzxbt/smm/src/strategy/marketmaker.py`
- `beatzxbt/smm/src/strategy/inventory.py`
- `beatzxbt/smm/src/strategy/oms.py`
- `beatzxbt/smm/src/strategy/features/`

### mm-toolbox

`master` is the default branch and provides Python/Numba implementations useful
for reading and adapting. Inspected the available branch tips and compared
`v1.1b` with master. The newer beta line, including `v1.0b9` and the recent
`review/beta-examples-benchmarks` branch, contains a substantial Cython-oriented
rewrite. Master is selected for accessible reference code rather than recency;
it is not the latest development version.

Start with:
- `beatzxbt/mm-toolbox/src/mm_toolbox/orderbook/`
- `beatzxbt/mm-toolbox/src/mm_toolbox/moving_average/`
- `beatzxbt/mm-toolbox/src/mm_toolbox/rounding/`
- `beatzxbt/mm-toolbox/src/mm_toolbox/ringbuffer/`
- `beatzxbt/mm-toolbox/tests/`

## Refreshing

To update a snapshot, clone the upstream repository into a temporary directory,
select the desired branch or commit, and replace the snapshot with its tracked
files only. Preserve its license and update the branch, commit, date, and rationale
above. Do not copy the upstream `.git` directory.
