"""Source connectors: each lands raw files, records a manifest, writes bronze parquet."""
from . import ecb, indpensim, worldbank

SOURCES = {
    "indpensim": indpensim.ingest,
    "worldbank_pinksheet": worldbank.ingest,
    "ecb_fx": ecb.ingest,
}
