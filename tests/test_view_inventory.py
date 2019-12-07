from wordstree.db import init_db, get_db


def signup_login(client):
    return client.post('/signup', data={
        'name': 'Person',
        'username': 'nick',
        'password': 'qwertY123',
        'password-confirm': 'qwertY123'}, follow_redirects=True)


def insert_branch(app, text, depth, ind, owner_id, sell):
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        db.execute(
            'INSERT INTO branches (ind, depth, length, width, angle, pos_x, pos_y, tree_id) VALUES'
            '(?, ?, ?, ?, ?, ?, ?, 1)',
            [ind, depth, 10, 10, 0.1, 0, 0]
        )
        branch_id = cur.execute('select last_insert_rowid()').fetchone()[0]
        db.execute(
            'INSERT INTO branches_ownership (branch_id, owner_id, text, available_for_purchase) VALUES'
            '(?, ?, ?, ?)',
            [branch_id, owner_id, text, sell]
        )
        db.commit()


def test_view_inventory(client, app):
    signup_login(client)
    insert_branch(app, "Branch 1", 1, 1, 1, 0)
    insert_branch(app, "Branch 2", 1, 2, 1, 0)
    rv = client.get('/inventory', follow_redirects=True)
    point = rv.data
    assert b'Branch 1' in point
    assert b'Branch 2' in point
