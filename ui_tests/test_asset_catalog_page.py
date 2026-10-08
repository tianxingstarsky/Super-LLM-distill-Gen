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
            if files := st.session_state.get("fixture-files"):
                return AssetInventory(tuple(Asset(f"{origin}/{path}", origin, path,
                                                  "来源文件" if origin == "source" else "样本", 5, 0)
                                            for origin, path in files))
            return AssetInventory(tuple(Asset(str(i), "source", f"File{i:04}.txt", "来源文件", 5, 0)
                                        for i in range(1002)))
        def download(self, workspace_id, asset):
            return b"hello"
        def excerpt(self, workspace_id, asset):
            return asset.name, ""
    render_asset_catalog(AssetCatalogApplication(Driver()), "test", show_title=False)


def test_file_pages_allow_reaching_all_assets_and_search_starts_on_first_page():
    app = AppTest.from_function(screen).run()
    assert not app.exception
    reached = []
    for page in range(1, 12):
        table = app.dataframe[0]
        assert table.key.startswith("asset-table:test:")
        names = table.value.iloc[:, 0].tolist()
        assert len(names) == (100 if page < 11 else 2)
        assert app.number_input[0].value == page
        reached.extend(names)
        # AppTest has no dataframe click helper. Send the documented selection
        # state through the real widget key and verify the selected file detail.
        app.session_state[table.key] = {"selection": {"rows": [len(names) - 1]}}
        app.run()
        assert not app.exception
        assert app.code[0].value == names[-1]
        if page < 11:
            app.button(key="asset-next:test").click().run()
            assert not app.exception
    assert reached == [f"File{i:04}.txt" for i in range(1002)]
    assert app.button(key="asset-next:test").disabled

    # A new search must begin at its first page even when the old view was on 11.
    app.text_input(key="asset-search:test").set_value("File0").run()
    assert not app.exception
    assert app.number_input[0].value == 1
    assert app.button(key="asset-prev:test").disabled
    assert app.dataframe[0].value.iloc[:, 0].tolist() == reached[:100]
    assert app.code[0].value == "File0000.txt"

    app.text_input(key="asset-search:test").set_value("File1001").run()
    assert not app.exception
    assert app.dataframe[0].value.iloc[:, 0].tolist() == ["File1001.txt"]
    assert app.code[0].value == "File1001.txt"
    assert not any(item.key == "asset-prev:test" for item in app.button)


def test_english_directory_translates_origin_prefixes_without_changing_chinese_paths():
    app = AppTest.from_function(screen)
    app.session_state["fixture-files"] = [
        ("source", "uploads/用户资料/首页.txt"), ("output", "exports/生成结果/样本.jsonl")]
    app.run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table["File name"].tolist() == ["首页.txt", "样本.jsonl"]
    assert table["Source / folder"].tolist() == [
        "Source / uploads/用户资料/首页.txt", "Result / exports/生成结果/样本.jsonl"]

    app.session_state["ui_language"] = "zh"
    app.run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table["文件名"].tolist() == ["首页.txt", "样本.jsonl"]
    assert table["来源 / 目录"].tolist() == [
        "来源 / uploads/用户资料/首页.txt", "产物 / exports/生成结果/样本.jsonl"]
