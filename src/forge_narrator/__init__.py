"""forge-narrator — NotebookForge SSML manifest → narrated audio + word timing.

A standalone tool for the Skitch Family Archive TTS feature. It consumes a
manifest zip exported by NotebookForge and produces the three S3 files
(``document.mp3``, ``document.marks.json``, ``document.blocks.json``).

See ``docs/TTS_Spec_B_AudioGenerator.md`` for the build contract.
"""

__version__ = "0.1.0"
