# Employer sponsorship facts

The optional sponsorship plugin shows employer reference matches only. It does not predict the outcome of a sponsorship application or inspect advertising text. It does not change the core analysis. Enable it from Settings > Plugins, or with:

```yaml
plugins:
  sponsorship:
    enabled: true
```

The plugin uses an accredited sponsor list, the Australian Border Force sanctions register, the ASX listed-company list and the ATO corporate tax transparency report. Exact and possible name matches are displayed separately. Recruiters and anonymous advertisers are marked as unknown because the actual employer is not identified. Data sources, file dates and report periods are shown with the facts. Name matching cannot establish that similarly named legal entities are the same organization.

Fetch may refresh ABF, ASX and ATO references. Analyze uses the locally stored references and makes no network request. A failed refresh retains existing files. Reference files live under the `sponsorship/data` directory inside the database-adjacent plugin directory described in [plugins.md](plugins.md).

The accredited list is a scanned Home Affairs FOI release. Supply a UTF-8 `accredited-YYYY-MM-DD.txt` file with one employer per line in that directory. The bundled `plugins/sponsorship/ocr_sponsor_pdf.ps1` can produce an OCR file on Windows. Check OCR output against the source before relying on a match. An employer absent from the list may still be eligible to sponsor.

The employer matching and reference loaders were adapted from PR #27 by Patrickchenxi, head `55e9079678c84ee2617b92b169570f5a2125f4ee`. Role mapping, salary thresholds, advertisement scans and likelihood scores from that PR are excluded from this version.

Reference sources: [Home Affairs FOI disclosure log](https://www.homeaffairs.gov.au/access-and-accountability/freedom-of-information/disclosure-logs), [ABF public register](https://www.abf.gov.au/about-us/what-we-do/sponsor-sanctions/register-of-sanctioned-sponsors), [ASX company directory](https://www.asx.com.au/asx/research/ASXListedCompanies.csv), and [ATO corporate tax transparency](https://data.gov.au/data/dataset/corporate-transparency). ABF currently serves the public register through a SharePoint data endpoint embedded in that page. It is not a versioned API; schema or download failures retain the previous cache and are recorded in plugin Debug output. Reference refreshes use a 30-day cache. Data can be incomplete or historical, so no match is never presented as a denial of sponsorship eligibility.
