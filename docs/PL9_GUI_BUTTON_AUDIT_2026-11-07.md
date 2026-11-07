# PL9 native GUI — evidence-based button and transition audit (2026-11-07)

## Verification rule
Every menu/button is NOT VERIFIED until: (1) input action recorded, (2) before/after screenshots or native UI state captured from the actual PL9.exe on GitHub Windows 2022, (3) resulting report/file/dialog content independently read. Sending keyboard/mouse input alone is not evidence.

## Recorded native runs
- [42331509804](https://github.com/zinevichroman-cpu/lokpa-public-runner/actions/runs/42331509804): native PL9.exe loaded smoke chart and wrote matching `Export/birthdat.txt` (33-column row). This verifies chart export, not interpretations.
- [42331699060](https://github.com/zinevichroman-cpu/lokpa-public-runner/actions/runs/42331699060): export modal dismissed; screenshots of PL9 native menus; application open, chart screen displayed. No verified full textual interpretation capture.
- [42331948852](https://github.com/zinevichroman-cpu/lokpa-public-runner/actions/runs/42331948852): verified screenshots of Reports > Interpretations > Dasha Interpretations, third-level entry, and native 5-level Dasha report period dialog. The report was NOT opened because the GUI controls were clicked using wrong unscaled coordinates.

## Confirmed navigation (from runner screenshots, not from memory)
| Route/action | Verified visible result | Evidence | Remaining |
|---|---|---|---|
| Chart open + Research > Export Birth Chart | PL9 displays confirmation and writes native birthdat.txt row matching requested chart fields | run 42331509804, AFTER_CALCULATION, API structured export | independent calculation methodology reconciliation |
| Close export confirmation | dialog disappears; natal chart remains visible | run 42331699060, AFTER_EXPORT_CONFIRMATION_CLOSE | none for this action |
| Reports top menu | 10 submenu entries visually present | run 42331699060, MENU_REPORTS | enumerate every submenu + action |
| Research top menu | Export Ephemeris/Export Birth Chart and other commands visible | run 42331699060, MENU_RESEARCH | test all items individually |
| Print top menu | dropdown visible | run 42331699060, MENU_PRINT | test all items individually |
| Reports > Interpretations | 7 families visible: General, Planetary, Bhavesh, Dasha, Transit, Classical, Life Interpretations | run 42331699060, REPORTS_INTERPRETATIONS_SUBMENU | open each third-level submenu & each report |
| Reports > Interpretations > Dasha Interpretations > Dasha report period | VERIFIED: third-level popup and report-period dialog, including selectable 1–5 levels | run 42331948852, DASHA_THIRD_LEVEL_MENU and DASHA_REPORT_PERIOD_BEFORE | select 5th level, click OK with measured coordinates, capture actual report text |
| Life Interpretations report | NOT VERIFIED | run 42331699060, LIFE report did not yield text | navigate & copy full native text |
| Interpretations text export | NOT VERIFIED | run 42331699060, clipboard sentinel unchanged | use native Copy/Save/Print and confirm content |

## Still untested — must not claim complete
File, Edit, Charts, Classical references, Options, Tools, Windows, all toolbar icons, all submenus of Reports/Research/Print, dialog controls, reports' copy/save/print actions, up to five dasha levels, life-area reports, individual planetary/house/yoga/transit interpretations.

## Source for navigation
The user's PL9 archive: `GeoVision/PL9/Res1/Manual/` including `chapter08.htm`, `chapter21.htm`, and `images/Feature57-1-ReportsMenu.png`. Screenshots must be reconciled with the native Windows runner for each verified route.

**Status of whole UI audit: NOT COMPLETE. Status of text extraction for requested chart: NOT COMPLETE.**
