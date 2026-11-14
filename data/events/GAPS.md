# Known gaps in the event calendars

| Area | Gap | Source checked | Effect |
|---|---|---|---|
| Unscheduled FOMC actions 2011–2013 | Not captured: the source used for 2011–2013 lists scheduled meetings only | https://www.federalreserve.gov/publications/annual-report/2011-Minutes-of-Federal-Open-Market-Committee-Meetings.htm | Unscheduled calls in 2011–2013 may be missing from unscheduled.csv |
| FOMC statement times 2010–2012 | 24 rows carry `14:15 (assumption, pre-2013 convention)`; the Fed historical pages give dates only | fomchistorical20YY.htm | Daily study unaffected (both candidate times are intraday); unusable for any intraday window |
| FOMC minutes 2012-10-24 | PDF URL returns 404; only an HTML minutes page exists | PDF: `monetarypolicy/files/fomcminutes20121024.pdf` (404); HTML: `monetarypolicy/fomcminutes20121024.htm` (200) | 1 of 133 meetings; DQ `minutes_missing` retained; retrieval-only, no result depends on it |
