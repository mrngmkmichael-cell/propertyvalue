# Flags

Nine 4x3 country flags for the language menu in the site header.

## Where they came from

All but one are from [flag-icons](https://github.com/lipis/flag-icons),
MIT licensed, copied in unchanged. They are plain paths: no scripts, no
external references, nothing fetched at render time. Self-hosted for the
same reason everything else here is (see `/privacy`: no third-party
requests run on this site).

`es.svg` is hand-written. The upstream Spanish flag carries the full
coat of arms and weighs 81 KB, which is more than the rest of the site's
icons put together, to draw a crest that is four pixels wide at the size
it renders. This is the same flag without the arms.

## Why these nine countries

A flag is a country and a language is not, so each one is a compromise
and the language's own name, next to it, is what actually identifies the
row. The mapping, and the reasoning where it is not obvious:

| File | Language | Note |
|---|---|---|
| `gb` | English | This is a UK site. |
| `hk` | Traditional Chinese | Hong Kong rather than Taiwan: the people arriving in the UK and buying UK property in numbers are BN(O) holders. Both read Traditional Chinese. |
| `cn` | Simplified Chinese | |
| `in` | Hindi | |
| `es` | Spanish | Most Spanish speakers do not live in Spain. No flag fixes that. |
| `sa` | Standard Arabic | Modern Standard Arabic belongs to no single country; Saudi Arabia is the usual stand-in. |
| `fr` | French | |
| `jp` | Japanese | |
| `kr` | Korean | |

## Adding a language

Drop the 4x3 SVG in here named after the ISO country code and add
`"flag"` to that language's entry in `app/i18n.py`. Nothing else reads
this directory.
