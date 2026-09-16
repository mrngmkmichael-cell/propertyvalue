# Doing the report's checks by hand: a timed run, 16 September 2026

**Question.** Every figure on the report comes from an official source that anyone can visit. What does it take to get the same answers by hand, for one address?

**Method.** One address, 55 Malden Hill Gardens, New Malden, KT3 4HX (chosen because it has a Land Registry sale, an EPC and a council tax band). Every card on the report was traced to the official website its figure comes from, and that website was worked through in a browser, one check after another, with a clock running from the moment each check started to the moment the answer was on screen. The person doing it already knew every site and what to type, drove the browser by script rather than by hand, and did the spreadsheet lookups with a script. So the clock is a floor: a buyer meeting these sites for the first time takes longer at every step, and the figures below do not guess how much longer.

**Headline.**

| Measure | Result |
|---|---|
| Checks on the report | 44 cards, done as 29 groups (some sites answer two or three cards) |
| Time on the clock | 35.8 minutes, the floor described above |
| Websites needed | 28 (27 official or open-data sites plus a postcode-to-area-code lookup), and one web search to find a council PDF |
| Steps, counted as a page load, a field typed, a list scrolled or a table read | about 190 |
| Downloads that need a spreadsheet or GIS program to read | 9 (air quality, sewage returns, household income, deprivation, three NHS files, Ofcom's coverage file, the council's admissions guide), of which 2 need GIS software (subsidence, historic landfill) |
| Answers that are a colour on a map, not a figure | 12 cards (flood, surface water, noise, radon, three planning cards, environmental designations, four mobile networks) |
| Answers that are behind a paid report | 3 (radon by address, subsidence by address, coal mining) |
| Dead ends met on the way | 13 (four 404s, two sites that would not open in this browser, one robot check, one map that ignored the postcode, one lookup site that did not answer, one alert box, and three searches that found nothing) |
| Figures that matched the report to the decimal | sold prices (29), price trend (+3.3%), council tax Band D (£2,608), EPC (D, 116 m²), air quality (1.67 times the WHO guideline), household income (£92,372), deprivation (decile 8), five census figures (61.3, 88.0, 32.9, 55.6 and 31.2 percent), broadband (gigabit), radon (under 1 in 100) |
| Figures the websites cannot give at all | crimes within a mile of the house (the website gives the police neighbourhood), sales within 0.6 miles (the search is by postcode or street), buses an hour (counted from timetables), patients per GP (three files), likelihood of a school place (the council's distance in a PDF against your own distance on a map) |

**What the run says.** The data is public and the work is not. Getting one address's 44 answers by hand means 28 websites, a spreadsheet program, a GIS program, three paid reports, a dozen colours read off maps, and the knowledge of which of three datasets called "Historic Landfill Sites" is the right one. That is the comparison the premium page can make honestly: not "our data", but "your afternoon".

## The checks, one by one

Minutes are the clock for that group. Steps are counted for a person, not for the script.

| Card(s) | Official source and site | What you need to know | Steps | Answer found (report's answer) | Min |
|---|---|---|---|---|---|
| Local Market | HM Land Registry price paid data, landregistry.data.gov.uk | Nothing beyond the postcode; the latest sale is found by scanning 19 entries | 3 | 29 sales for 19 properties (29 sales, £823,500 last) | 0.7 |
| Valuation Estimate | No official tool | The nearest is the same search by street and your own median over the rows | 0 | Not available by hand (the report's median of 300 sales within 0.6 miles) | included above |
| Area Prosperity, Price Trend | UK House Price Index, landregistry.data.gov.uk/app/ukhpi | The borough's name, the data-table tab, and the five-year change is your own division | 8 | £591,555, +3.3% on the year; +12.4% over five years (+3.3%, five-year figure on the card) | 1.1 |
| Council Tax | VOA band lookup on tax.service.gov.uk, then the council's charges table | Two sites; which column of a five-column table applies | 7 | Band E, £3,187.71; Band D £2,608.12 (every band's bill, Band D £2,609) | 1.3 |
| Costs and Affordability | SDLT calculator, tax.service.gov.uk | Eleven question pages, once per buyer type | 11 (33 for the three figures the card shows) | £31,175 standard rate (standard, first-time and additional side by side) | 2.8 |
| Rental Analysis | ONS Price Index of Private Rents, ons.gov.uk | The borough name and the "Housing prices in your area" tool | 5 | £1,811 a month, August 2026 (£1,807, the previous release) | 1.1 |
| Energy Efficiency, EPC cost to C | EPC register, find-energy-certificate.service.gov.uk | The address in a 20-row list; the cost to C is read from four improvement steps | 3 plus reading | D, 116 m², £974 a year, wall insulation to reach C (same) | 0.8 |
| Extended or Modified | Council planning register (Idox), publicaccess.kingston.gov.uk | That the council runs an Idox portal and that no result means no application, not no extension | 3 | 10 applications on the street, none at number 55 (search with a house number) | 1.8 |
| Aspect | A map, openstreetmap.org | A compass and a guess | 2 | Garden side judged by eye (Garden faces South) | 0.5 |
| Flood Risk, Surface Water | Environment Agency: the address check sits behind a robot check; the flood map for planning gives the zone as shading | Zones 2 and 3 are shaded, no shading is zone 1; the map opens at borough scale with no marker | 6 | Read as a colour after zooming to the street by hand (Zone 1, very low surface water) | 2.0 |
| Sewage Discharge | Thames Water storm discharge map; the yearly count is the Environment Agency's annual EDM return | The map says discharging now or not; the year's count is a national spreadsheet found by grid reference | 4 plus a download | Nearest monitor's status only (1 spill nearby in 2025) | 0.7 |
| Noise | DEFRA strategic noise map via the Extrium viewer | A colour band off a legend, Lden, at a map that stayed at country scale after the postcode | 5 plus a dozen zooms | A colour range (56 dB(A), Moderate) | 1.2 |
| Crime and Safety | police.uk | The police neighbourhood is whatever shape it is | 4 | 73 crimes in July 2026 for New Malden Village (229 within a mile) | 0.9 |
| In the News? | No register exists | Nothing to look up; the report says so | 0 | (No register) | 0 |
| Radon Gas | UKHSA radon map, ukradon.org | A colour against a legend; the address report is paid | 5 | Under 1 in 100 homes (Low, under 1%) | 1.1 |
| Subsidence Risk | BGS GeoIndex has no shrink-swell layer; GeoClimate is a GIS download or a paid GeoReport | GIS software | 4 to find that out | Not readable on a free map (Improbable by 2030) | 1.4 |
| Air Quality | DEFRA background maps, uk-air.defra.gov.uk | The house's OS easting and northing from another site, the 1km square, a 7.9 MB file of 254,911 rows, and the WHO guideline | 8 plus a spreadsheet | PM2.5 8.36, which is 1.67 times the guideline (1.7 times WHO guideline) | 0.8 |
| Historic Contamination | Environment Agency historic landfill dataset via data.gov.uk | Which of three datasets with the same name; then GIS software for a shapefile | 6 plus GIS | Not readable without GIS (None nearby) | 1.2 |
| Mining Risk | gov.uk, the Mining Remediation Authority | The old map is a 404; the search is free, the report is £27 plus VAT | 4, two dead ends | Paid report (Data unavailable today) | 2.1 |
| Planning Constraints, Development Nearby, Listed Buildings | planning.data.gov.uk map | Which 7 of 57 layers to tick, and that the page warns its data may be incomplete | 8 | Unshaded ground at the house (None found; 2 register sites; 1 listed nearby) | 0.9 |
| Environmental Designations | Natural England MAGIC map | Would not open in this browser; dozens of designation layers for a person | not measured | (None found) | 0.9 |
| Listed Buildings | Historic England list map search | A cookie dialog with no reject button; a click per pin | 5 plus a click per pin | Pins on a map (1 nearby) | 1.1 |
| Schools Nearby, State, Private, Universities | Get Information about Schools, then Ofsted per school | Filters for phase, type and status over 235 establishments within a mile; one Ofsted page per URN | 6 plus 2 per school | Grade per school one at a time (76 schools within three miles, rated) | 1.5 |
| School Catchment Areas | The council's admissions guide, a 40-page PDF found by web search | That Kingston's admissions are run by Achieving for Children; then your own distance on a map per school | 6 per school | Distances in a PDF (4 likely, 0 borderline, 4 unlikely) | 1.3 |
| Nearby Essentials | A map app, one search per category | Six searches and reading a distance off each | 6 | One shop per text search (nearest of each with distances) | 0.7 |
| Getting Around | A map app's walking directions | Find the station first, then route to it | 4 per station | 661 m, 9 minutes to New Malden station (stations with walking times) | 0.4 |
| Health Services | NHS find a GP, then three NHS Digital files | The practice code, two national files matched on it, a division; A&E is a third file | 3 plus three downloads | Nearest practice 0.4 miles (1,791 patients per GP) | 0.8 |
| Bus Service | bustimes.org (Bus Open Data) | The stops nearest the house, then counting departures from a timetable | 6 plus counting | 32 stops, 11 routes (16.5 an hour, weekday daytime) | 0.8 |
| Broadband, Mobile Signal | Ofcom checkers | The address in a list of 23; four coverage maps read by colour behind a reCAPTCHA | 9 | Ultrafast 5,000 Mbps; four coverage maps (Gigabit-capable; 99.99% 4G outdoor) | 3.4 |
| Household Income | ONS income estimates for small areas | The house's MSOA code from a lookup, a 1.8 MB workbook, the right sheet | 4 plus a spreadsheet | £92,372 (£92,372) | 0.7 |
| Deprivation, Since 2011, Occupation, Qualification, Age Profile, Housing, Ethnicity and Religion, Health and Wellbeing | gov.uk indices of deprivation 2025 (File 1 of 15); Nomis census area profile | The house's LSOA code from a lookup; a 33,756-row file; eight census tables to read and six sums to do; the 2011 profile as well for the change | 5 plus 2 pages, 8 tables, 6 sums | Decile 8; 61.3, 88.0, 32.9, 55.6, 31.2 percent (the same) | 1.9 |

## Notes on honesty

- The clock includes the browser tool's own delays and excludes a person's reading time, so it is neither a human time nor a machine time. It is presented as a floor and nothing else.
- Three sources could not be reached in this browser (the Environment Agency address check behind its robot check, Natural England's MAGIC map, the old Coal Authority map). The steps for them are described from their pages, not measured.
- The spreadsheet lookups (air quality, income, deprivation) were done by script; a person needs a spreadsheet program and the patience to find one row in a quarter of a million.
- Where the website's answer differs from the report's, the reason is geography, not error: police.uk gives the police neighbourhood, the report gives a one-mile radius from the same data; the rent figure moved by one monthly release.

## What this is for

The premium page can now carry a "doing this yourself" table with counted facts (28 sites, 9 downloads, 3 paid reports, 12 colours read off maps, 13 dead ends, 36 minutes for someone who already knew every site) rather than an estimate. Every row above names the site, so anyone can repeat it.

---

# 用人手做報告的檢查：2026 年 9 月 16 日的計時測試

**問題。** 報告上每個數字都來自任何人都可以瀏覽的官方來源。用人手為一個地址取得同樣的答案，要付出什麼？

**方法。** 一個地址（New Malden 的 55 Malden Hill Gardens，KT3 4HX）。報告上每張卡片都追溯到它的官方網站，然後在瀏覽器裡逐個網站做，每項檢查從開始到答案出現在螢幕上都計時。做的人已經知道每個網站和要輸入什麼，用腳本驅動瀏覽器，試算表的查找也用腳本完成。所以這個時間是下限：第一次接觸這些網站的買家每一步都會更慢，而以下數字不猜測慢多少。

**結果。** 44 張卡片，分 29 組完成，計時 35.8 分鐘（下限）。需要 28 個網站，加一次網上搜尋才找到議會的 PDF；約 190 個步驟；9 個需要試算表或 GIS 軟件才能讀的下載，其中 2 個要 GIS；12 張卡片的答案是地圖上的顏色而不是數字；3 項要付費報告（氡氣、地陷、煤礦，煤礦報告 £27 加增值稅起）；沿途 13 個死胡同。與報告完全一致的數字：售價 29 宗、樓價升幅 +3.3%、Band D 市政稅 £2,608、EPC D 級 116 平方米、空氣質素 1.67 倍世衞指引、家庭收入 £92,372、貧困指數第 8 十分位、五個人口普查數字（61.3、88.0、32.9、55.6、31.2%）、寬頻千兆、氡氣低於百分之一。網站根本給不出的數字：一英里內的罪案（網站只給警區）、0.6 英里內的售價、每小時巴士班次、每名醫生的病人數、入學機會。

**結論。** 資料是公開的，工作不是。用人手取得一個地址的 44 個答案，需要 28 個網站、一個試算表程式、一個 GIS 程式、三份付費報告、十幾個要在地圖上讀顏色的答案，以及知道三個同名為「Historic Landfill Sites」的資料集哪一個才對。這就是 Premium 頁可以誠實地作的比較：不是「我們的資料」，而是「你的一個下午」。
