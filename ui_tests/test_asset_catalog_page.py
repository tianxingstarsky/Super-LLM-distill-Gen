from streamlit.testing.v1 import AppTest


def screen():
    import streamlit as st
    from lib.application.asset_catalog_service import AssetCatalogApplication
    from lib.domain.dataset_assets import Asset, AssetInventory
    from lib.presentation.streamlit.asset_catalog_page import render_asset_catalog
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    st.session_state.setdefault("ui_language", "en")
    install_streamlit_localization()
    class Driver:
        def inventory(self, workspace_id):
            return AssetInventory(tuple(Asset(str(i), "source", f"File{i:03}.txt", "来源文件", 5, 0)
                                        for i in range(105)))
        def download(self, workspace_id, asset):
            return b"hello"
        def excerpt(self, workspace_id, asset):
            return asset.name, ""
    render_asset_catalog(AssetCatalogApplication(Driver()), "test", show_title=False)


def test_file_pages_allow_reaching_all_assets_and_search_starts_on_first_page():
    app = AppTest.from_function(screen).run()
    assert not app.exception
    selected = next(item for item in app.selectbox if item.key.startswith("asset-selected:"))
    assert len(selected.options) == 100
    app.button(key="asset-next:test").click().run()
    assert not app.exception
    selected = next(item for item in app.selectbox if item.key.startswith("asset-selected:"))
    assert len(selected.options) == 5
    assert "File104.txt" in selected.options[-1]
    assert app.button(key="asset-next:test").disabled
    app.text_input(key="asset-search:test").set_value("File104").run()
    assert not app.exception
    selected = next(item for item in app.selectbox if item.key.startswith("asset-selected:"))
    assert len(selected.options) == 1 and "File104.txt" in selected.options[0]
    assert not any(item.key == "asset-prev:test" for item in app.button)
