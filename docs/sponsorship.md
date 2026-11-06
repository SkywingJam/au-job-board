# Employer sponsorship facts

The optional sponsorship plugin shows employer reference matches. It does not decide whether a job offers sponsorship or change core analysis. Enable it after reviewing and accepting the permissions in Settings > Plugins, or use the CLI described in [plugins.md](plugins.md).

The package contains public reference baselines for the Home Affairs accredited sponsor list, ABF sanctions register, ASX listed companies and the ATO corporate tax transparency report. Their source URLs, retrieval date and SHA-256 hashes are in `plugins/sponsorship/baseline/provenance.json` and `accredited-provenance.json`. The Home Affairs list is OCR-derived and has not been fully manually transcribed. Dates and sources appear with employer facts; old data is marked where a source date is available. Exact and possible name matches are distinct. Recruiters and anonymous advertisers have unknown actual employers.

After a fetch, the host may refresh ABF, ASX and ATO public files. It validates a download before replacing the cache. A failed or invalid update retains the previous file. Analysis uses a valid cache, falling back independently to the bundled baseline if needed. User-imported accredited lists are separate and are not overwritten by refresh. Reference files live in the database-adjacent plugin `sponsorship/data` directory.

## Updating the accredited list manually

1. Obtain a current public scanned release from the [Home Affairs FOI disclosure log](https://www.homeaffairs.gov.au/access-and-accountability/freedom-of-information/disclosure-logs). Record its source and date.
2. Extract text with an OCR tool. On Windows, `plugins/sponsorship/ocr_sponsor_pdf.ps1` is available; review its usage and prerequisites before running it. OCR is not performed automatically.
3. Check the extracted employer names against the PDF, especially page boundaries, headers and ambiguous characters. Keep one name per line in UTF-8.
4. Place the result in the plugin data directory as `accredited-YYYY-MM-DD.txt`. Retain the source and review notes separately. Analyze again to update plugin results. Removing an imported file makes the bundled baseline available again.

The matching and reference loaders were adapted from PR #27 by Patrickchenxi, head `55e9079678c84ee2617b92b169570f5a2125f4ee`. Name matching can be uncertain and a list entry does not establish sponsorship for an individual role.
