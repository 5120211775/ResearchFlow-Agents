"""Folders in the corpus (RFC V3.1 §7.1, PR-14; AC-18, AC-22)."""
from __future__ import annotations
import httpx
import pytest
from app.errors import Invalid
from app.services.corpus_ingest import folder_components, ingest_document
from app.services.corpus_selection import freeze_selection
from research_engine.corpus import CorpusStore
from tests.dataflow.test_corpus_store import SOLAR_TEXT, VENTS_TEXT, FakeEmbeddings
TOKEN = 'folders-token'

@pytest.fixture
async def store(tmp_path) -> CorpusStore:
    return CorpusStore(tmp_path / 'c.sqlite', FakeEmbeddings())

@pytest.mark.parametrize(('path', 'folders'), [('Thesis/Ch2/data.csv', ['Thesis', 'Ch2']), ('Thesis\\Ch2\\data.csv', ['Thesis', 'Ch2']), ('data.csv', []), (None, [])])
def test_a_relative_path_names_folders(path, folders):
    assert folder_components(path) == folders

@pytest.mark.parametrize('path', ['../etc/passwd', 'Thesis/../../x.csv', 'Thesis//x.csv', './x.csv', 'a/\x00/x.csv'])
def test_a_path_that_could_climb_or_alias_is_refused(path):
    with pytest.raises(Invalid):
        folder_components(path)

def test_an_absurdly_deep_path_is_refused():
    with pytest.raises(Invalid, match='levels deep'):
        folder_components('/'.join(['d'] * 40) + '/x.csv')

async def test_a_folder_upload_files_documents_and_creates_folders(store):
    await ingest_document(store, 'solar.txt', SOLAR_TEXT.encode(), relative_path='Thesis/Ch1/solar.txt')
    await ingest_document(store, 'vents.txt', VENTS_TEXT.encode(), relative_path='Thesis/Ch2/vents.txt')
    names = {f['name']: f for f in await store.folders()}
    assert set(names) == {'Thesis', 'Ch1', 'Ch2'}
    assert names['Ch1']['parent_id'] == names['Thesis']['id']
    assert len(await store.memberships()) == 2

async def test_the_same_file_in_two_folders_is_one_document_two_memberships(store):
    first = await ingest_document(store, 'solar.txt', SOLAR_TEXT.encode(), relative_path='A/solar.txt')
    again = await ingest_document(store, 'solar.txt', SOLAR_TEXT.encode(), relative_path='B/solar.txt')
    assert first.id == again.id
    assert len(await store.documents()) == 1
    assert {m['folder_id'] for m in await store.memberships()} == {f['id'] for f in await store.folders()}

async def test_rename_and_move_keep_ids_and_refuse_a_cycle(store):
    parent = await store.create_folder('Thesis')
    child = await store.create_folder('Ch1', parent)
    await store.update_folder(child, name='Chapter 1')
    await store.update_folder(child, parent_id=None)
    folders = {f['id']: f for f in await store.folders()}
    assert folders[child]['name'] == 'Chapter 1' and folders[child]['parent_id'] is None
    await store.update_folder(child, parent_id=parent)
    with pytest.raises(ValueError, match='into itself'):
        await store.update_folder(parent, parent_id=child)

async def test_deleting_a_folder_deletes_only_documents_filed_nowhere_else(store):
    only = await ingest_document(store, 'solar.txt', SOLAR_TEXT.encode(), relative_path='Old/solar.txt')
    both = await ingest_document(store, 'vents.txt', VENTS_TEXT.encode(), relative_path='Old/vents.txt')
    await ingest_document(store, 'vents.txt', VENTS_TEXT.encode(), relative_path='Keep/vents.txt')
    loose = await store.ingest('loose.txt', b'A document never filed in any folder at all.')
    old = next((f['id'] for f in await store.folders() if f['name'] == 'Old'))
    result = await store.delete_folder(old)
    remaining = {d['id'] for d in await store.documents()}
    assert only.id not in remaining, 'filed only in the deleted folder'
    assert both.id in remaining, 'still filed in another folder'
    assert loose.doc_id in remaining, 'never in a folder, never touched'
    assert result['documents_deleted'] == 1

async def test_a_folder_selection_includes_its_subfolders_resolved_now(store):
    await ingest_document(store, 'solar.txt', SOLAR_TEXT.encode(), relative_path='Thesis/Ch1/solar.txt')
    await ingest_document(store, 'vents.txt', VENTS_TEXT.encode(), relative_path='Elsewhere/vents.txt')
    thesis = next((f['id'] for f in await store.folders() if f['name'] == 'Thesis'))
    frozen = await freeze_selection(store, folder_ids=[thesis], doc_keys=[], whole_corpus=False)
    assert [i['filename'] for i in frozen['resolved']] == ['solar.txt']
