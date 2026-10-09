"""Responsive layout for the local image-and-text authoring surface."""

MANUAL_DATASET_STYLE = """<style>
.st-key-manual-datasets-picker,
.st-key-manual-datasets-editor,
.st-key-manual-datasets-preview,
.st-key-manual-datasets-saved { min-width:0; }
.st-key-manual-datasets-workbench [data-testid="stColumn"] { min-width:0; }
.st-key-manual-datasets-preview [data-testid="stText"],
.st-key-manual-datasets-saved [data-testid="stText"] { overflow-wrap:anywhere; }
@media(max-width:720px) {
  /* Stack only the authoring page's direct rows. Image thumbnails remain
     grouped instead of inheriting a global override of Streamlit columns. */
  .st-key-manual-datasets-picker > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  .st-key-manual-datasets-workbench > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  .st-key-manual-datasets-saved > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
    display:grid !important;grid-template-columns:minmax(0,1fr);gap:12px;
  }
  .st-key-manual-datasets-picker > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-manual-datasets-workbench > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-manual-datasets-saved > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex:none !important;width:100% !important;min-width:0 !important;max-width:100%;
  }
  .st-key-manual-datasets-picker button,
  .st-key-manual-datasets-editor button,
  .st-key-manual-datasets-saved button { min-height:44px; }
  .st-key-manual-datasets-editor [data-testid="stFileUploaderDropzone"] { min-width:0;flex-wrap:wrap;gap:10px; }
  .st-key-manual-datasets-editor [data-testid="stFileUploaderDropzoneInstructions"] { min-width:0; }
  .st-key-manual-datasets-editor,
  .st-key-manual-datasets-preview,
  .st-key-manual-datasets-saved { padding:15px; }
}
</style>"""
