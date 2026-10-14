# Note to the Overpass API operators (FOSSGIS), 14 Oct 2026

Optional courtesy note for Michael to send. The block is fair: the public
Overpass instance asks commercial users to self-host or pay, asks a website
to stay under about 100 requests a day across all its users, and asks every
client to identify itself uniquely. UKPropertyInsight did none of the three
(it sent a generic "curl" identity after an earlier refusal). The site stopped
calling Overpass on 14 Oct 2026 and now reads stations from NaPTAN and nearby
places from the Food Standards Agency and NHS England, so nothing is being
asked of them; this only closes the loop.

Where to send it: the operator is FOSSGIS e.V., linked from the "Public
Overpass API instances" table on wiki.openstreetmap.org/wiki/Overpass_API
(https://www.fossgis.de/aktivitäten/langzeitförderungen/overpass/). Use the
contact given there.

---

Subject: UKPropertyInsight has stopped using overpass-api.de

Hello,

I run ukpropertyinsight.co.uk, a small UK property-data site. Its property
reports used the public Overpass instance at overpass-api.de to look up
stations, shops and building outlines near an address. Your server now
refuses requests from the site, and having read the usage policy I
understand why: our use was commercial, it ran well above the volume the
policy describes for a website, and after an earlier refusal it sent a
generic client identity instead of identifying itself.

I am sorry for the load that caused. As of 14 October 2026 the site sends
no requests to overpass-api.de or any other public Overpass instance; it
now uses official open datasets instead. Nothing needs doing on your side.

Thank you for running the service for the community.

Michael
UKPropertyInsight, ukpropertyinsight.co.uk
