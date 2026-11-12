# Known gaps in the event calendars

| Area | Gap | Source checked | Effect |
|---|---|---|---|
| Unscheduled FOMC actions 2011–2013 | Not captured: the source used for 2011–2013 lists scheduled meetings only | https://www.federalreserve.gov/publications/annual-report/2011-Minutes-of-Federal-Open-Market-Committee-Meetings.htm | Unscheduled calls in 2011–2013 may be missing from unscheduled.csv |
| FOMC statement times 2010–2012 | 24 rows carry `14:15 (assumption, pre-2013 convention)`; the Fed historical pages give dates only | fomchistorical20YY.htm | Daily study unaffected (both candidate times are intraday); unusable for any intraday window |
