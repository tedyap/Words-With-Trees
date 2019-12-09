from typing import List
import random
import time
import json
import os

from flask import current_app, Flask
import sqlite3

from wordstree.db import get_db
from wordstree.graphics import *
from wordstree.graphics.util import Vec, radians, JSONifiable, create_file, open_file
from wordstree.graphics.branch import Branch

BRANCH_LENGTH_SHRINK_FACTOR = 1 / math.sqrt(2)
# BRANCH_LENGTH_SHRINK_FACTOR = 0.97
BRANCH_WIDTH_SHRINK_FACTOR = 0.8
BRANCH_LENGTH_DELTA = 0.01
BRANCH_ANGLE_DELTA = radians(10)
# BRANCH_ANGLES = (radians(20), radians(-20))
BRANCH_ANGLES = (1, -1)
MAX_BRANCH_LENGTH = 0.2
MAX_CHILDREN = 2


def _remove_file_ext(fname: str) -> str:
    # remove file name extension (the part after the period '.') from file name
    # if one exists
    period_indx = fname[::-1].find('.')
    if period_indx < 0:
        period_indx = 0
    return fname[0:len(fname)-1-period_indx]


def _get_default_tree_name():
    def_name = hex(int(time.time()))
    return def_name[2:]


def generate_root() -> Branch:
    return Branch(
        0,
        Vec(0.5, 0.99),
        **{
            'length': 0.4,
            'width': 0.008
        }
    )


def generate_branches(parent: Branch, index: int, layer: int) -> List[Branch]:
    if layer == 0:
        return [generate_root()]

    if layer > 10:
        num_branches = math.floor(max(0, random.gauss(0.5 - 0.5 * layer, 0.5)) + 0.5)
    else:
        num_branches = MAX_CHILDREN

    nangles = len(BRANCH_ANGLES)
    branches = []

    ppos, plength, pwidth, pangle = parent.pos, parent.length, parent.width, parent.angle

    base_length = min(plength, MAX_BRANCH_LENGTH) * BRANCH_LENGTH_SHRINK_FACTOR

    for i in range(num_branches):
        width = pwidth * BRANCH_WIDTH_SHRINK_FACTOR
        # length = max(base_length + random.gauss(0, BRANCH_LENGTH_DELTA) / (layer + 1), 0)
        length = base_length
        # angle = pangle + BRANCH_ANGLES[i % nangles] + random.gauss(0, BRANCH_ANGLE_DELTA)
        angle = pangle + BRANCH_ANGLES[i % nangles] * math.pi / 2

        x = ppos.x + math.cos(pangle) * plength
        y = ppos.y + math.sin(pangle) * plength

        branch = Branch(
            index + i,
            Vec(x, y),
            **{
                'length': length,
                'angle': angle,
                'parent': parent,
                'width': width,
                'depth': layer
            }
        )
        branches.append(branch)

    return branches


def create_branches(branches: List, max_layers=13):
    begin, end, layer = -1, 0, 0
    max_length = len(branches)

    if max_length < 1:
        return 0

    # create root branch
    branches[end] = generate_root()
    begin += 1
    end += 1
    layer += 1

    layers = []
    while layer < max_layers:
        layers.append(begin)

        layer_end = end
        while begin < layer_end:
            parent = branches[begin]
            sub_branches = generate_branches(parent, end, layer)

            i, size = 0, len(sub_branches)
            while i < size and end < max_length:
                branches[end] = sub_branches[i]
                i += 1
                end += 1
            begin += 1

        if begin == end:
            # no new branches were added
            break

        layer += 1

    # return number of branches created
    return layers, end


def generate_tree(max_depth=10, cls=None) -> Tuple[List, List[int], int]:
    print('Generating new tree max_depth={} ...'.format(max_depth))

    branches = [None for i in range(2 ** max_depth + 1)]
    layers, length = create_branches(branches, max_layers=max_depth)

    print('  layers: {}'.format(layers))
    print('  number of branches: {}'.format(length))
    return branches, layers, length


class Loader:
    """Abstract class for saving/loading branches and tile metadata from storage"""

    def load_branches(self, **kwargs):
        """Load branches from storage(database, disk, etc)"""
        pass

    def save_branches(self, **kwargs):
        """Save branch information to storage(database, disk, etc)"""
        pass

    def save_tile_info(self, **kwargs):
        """Save tile information to storage(database, disk, etc)"""
        pass

    def save_zoom_info(self, **kwargs):
        """Save information about zoom level (tile width, height, tree_id etc.)"""
        pass

    def save_branches_from_loader(self, loader):
        """same as `save_branches` but list of branches is retrieved from `loader.branches`"""
        pass

    def _branches(self) -> List[Branch]:
        """Return help copy of list of branches"""
        pass

    def _num_branches(self) -> int:
        """Return the number of branches currently stored in memory"""
        pass

    def _layers(self) -> List[int]:
        """Return list of indices for `self.branches` list that correspond to the beginning of a new tree layer"""
        pass

    def _input_tree_dict(self) -> dict:
        """Returns dictionary containing information about most recently loaded tree"""
        pass

    def _output_tree_dict(self) -> dict:
        """Returns dictionary containing information about most recently saved tree"""
        pass

    def input_tree(self, prop):
        """Returns property `prop` of tree most recently loaded/generated by this loader instance.
        eg. passing 'name' for :param:`prop` will return name of tree, if one exists
        :param prop: name of property
        """
        dic = self._input_tree_dict()
        if dic is None:
            return None
        else:
            return dic.get(prop, None)

    def output_tree(self, prop):
        """Returns property `prop` of tree most recently saved(or to be saved) by this loader instance.
        :param prop: name of property
        """
        dic = self._output_tree_dict()
        if dic is None:
            return None
        else:
            return dic.get(prop, self.input_tree(prop))

    @property
    def branches(self) -> List[Branch]:
        return self._branches()

    @property
    def num_branches(self) -> int:
        return self._num_branches()

    @property
    def layers(self) -> List[int]:
        return self._layers()


class BranchJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, JSONifiable):
            return o.json_obj()
        return json.JSONEncoder.default(self, o)


def _as_obj_hook(dic):
    if 'name' in dic:
        return dic

    for key in Branch.JSON_KEYS:
        if key not in dic:
            return dic

    pos_dict = dic['pos']
    pos = Vec(pos_dict['x'], pos_dict['y'])
    branch = Branch(dic['index'], pos,
                    depth=dic['depth'],
                    length=dic['length'],
                    width=dic['width'],
                    angle=dic['angle']
                    )
    return branch


class DBLoader(Loader):
    """Save/load branches and tile metadata to database"""

    def __init__(self, app: Flask):
        self.__app = app
        self.__branches = None
        self.__layers = None
        self.__num_branches = 0

        self.__input_tree = dict()
        self.__output_tree = dict()

        # dictionary of zoom level and a tuple of the surface where the tree was drawn at that zoom level
        # and the Loader instance containing the list of branches
        self.__map = {}

    def load_branches(self, **kwargs):
        tree_id = kwargs.get('tree_id', None)
        tree_name = kwargs.get('tree_name', None)
        new_tree = tree_id is None

        if new_tree:
            max_depth = kwargs.get('max_depth', 10)
            self.__branches, self.__layers, self.__num_branches = generate_tree(max_depth=max_depth)
            if tree_name:
                self.__input_tree['tree_name'] = tree_name
            else:
                self.__input_tree['tree_name'] = _get_default_tree_name()
            print('DB Loader')
            print(self.layers)
        else:
            self.__read_all_branches(tree_id)

        # empty map
        self.__map = {}

    def save_zoom_info(self, **kwargs):
        """
        Add zoom level information to database.
        :param tree_id: id of the corresponding entry in `tree` table; if none is provided, then assumed to the same
            as the tree that was most recently loaded with :meth:`load_branches`
        :param zoom_level: level of zoom
        :param grid: size of the square grid
        :param tile_size: (width, height) of the tile
        :param img_size: (width, height) of the tile image, i.e dimensions of the image
        :param img_dir: path to the directory containing the images of the tile
        :param json_dir: path to the directory containing the JSON files that contain list of branches
            contained in a tile
        :return:
        """
        req_args = ['tree_id', 'zoom_level', 'grid', 'tile_size', 'img_size', 'img_dir', 'json_dir']
        if not kwargs.get('tree_id', None):
            kwargs['tree_id'] = self.output_tree('tree_id')

        for key in req_args:
            if kwargs.get(key, None) is None:
                raise ValueError('{} cannot be None'.format(key))

        tree_id = kwargs['tree_id']
        zoom_level = kwargs['zoom_level']
        grid = kwargs['grid']
        tile_width, tile_height = kwargs['tile_size']
        img_width, img_height = kwargs['img_size']
        img_dir = kwargs['img_dir']
        json_dir = kwargs['json_dir']

        with self.app.app_context():
            db = get_db()
            cur = db.cursor()
            cur.execute('SELECT zoom_id FROM zoom_info WHERE zoom_level=? AND tree_id=?', [zoom_level, tree_id])
            res = cur.fetchone()
            if res is not None:
                # drop existing entry with same zoom_level and tree_id
                cur.execute('SELECT tile_id FROM tiles WHERE "zoom_id"=?', [res['zoom_id']])
                res2 = cur.fetchall()
                print('  Dropping existing zoom level info and {} associated tiles ...'.format(len(res2)))
                cur.execute('DELETE FROM tiles WHERE "zoom_id"=?', [res['zoom_id']])
                cur.execute('DELETE FROM zoom_info WHERE "zoom_id"=?', [res['zoom_id']])

            cur.execute(
                'INSERT INTO zoom_info (zoom_level, tree_id, grid, tile_width, tile_height, image_width, image_height,'
                ' imgs_path, jsons_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [zoom_level, tree_id, grid, tile_width, tile_height, img_width, img_height, img_dir, json_dir]
            )
            cur.execute('SELECT last_insert_rowid();')
            zoom_id = cur.fetchone()[0]

            self.__map[(tree_id, zoom_level)] = zoom_id

            db.commit()

        print('  Added zoom level {}, {} {:.2f}x{:.2f} tiles'.format(zoom_level, grid, tile_width, tile_height))

    def save_tile_info(self, **kwargs):
        """
        Save tile information to `tiles` table of application database. Will raise exception if zoom information
        has not already been added to `zoom_info` table.
        :param tree_id: `tree_id` column value of the entry in `tree` table that this tile belongs to
        :param zoom_level: zoom level that the tile is being rendered at
        :param tile_path: path to the PNG image of the tile
        :param json_path: path to the JSON file containing list of branches visible in the tile
        :param grid_location: tuple `(i, j)` where `i` is the zero-indexed row of the tile, `j` is the column
        :param tile_index: index of the grid if all the squares of the grid were numbered starting from 0, should be
            equivalent to `i*(num of tiles per row) + j` where `(i, j)` refer to row and column number of the tile
            respectively
        :param tile_position: tuple `(x, y)` of the position of the top-left corner of the tile in the full rendering
            of the tree
        """
        req_args = ['tree_id', 'zoom_level', 'tile_index',  'grid_location', 'tile_position']
        if not kwargs.get('tree_id', None):
            kwargs['tree_id'] = self.output_tree('tree_id')

        for key in req_args:
            if kwargs.get(key, None) is None:
                raise ValueError('{} cannot be None'.format(key))

        zoom_level = kwargs['zoom_level']
        tree_id = kwargs['tree_id']
        img_path = kwargs.get('img_path', '')
        json_path = kwargs.get('json_path', '')
        index = kwargs['tile_index']
        row, col = kwargs['grid_location']
        x, y = kwargs['tile_position']

        with self.app.app_context():
            db = get_db()
            cur = db.cursor()

            zoom_id = self.__map.get((tree_id, zoom_level), None)
            if zoom_id is None:
                cur.execute('SELECT zoom_id FROM zoom_info WHERE zoom_level=? AND tree_id=?', [zoom_level, tree_id])
                result = cur.fetchone()
                if len(result) == 0:
                    raise Exception('tree-id {}, zoom level {} information not added to zoom_info table'
                                    .format(tree_id, zoom_level))
                else:
                    zoom_id = result[0]

            cur.execute(
                'INSERT INTO tiles (tile_index, zoom_id, img_file, json_file, tile_col, tile_row, tile_pos_x, '
                'tile_pos_y) VALUES (?,?,?,?,?,?,?,?)',
                [index, zoom_id, img_path, json_path, col, row, x, y]
            )

            db.commit()

    def update_branches(self, tree_id, **kwargs):
        """
        Update branch entries with tree-id `tree-id` with information in contained is the list of :class:`Branch`
        objects in `branches`. If entries have same index (i.e `ind` column value same as :meth:`Branch.index`), then
        the entry will be updated. If such an entry does not exist, it will be inserted.

        :param tree_id: id of entry in the `tree` table
        :param branches: list of :class:`Branch` objects to insert into or update in the `branches` table
        :param num_branches: number of :class:`Branch` objects containing in the `branches` list; if not provided,
            defaults to `len(branches)`
        :param kwargs: additional options
        :return:
        """
        # use self.branches if branches kwarg not provided
        branches, num_branches = kwargs.get('branches', None), kwargs.get('num_branches', None)
        if branches is None:
            branches = self.branches
            num_branches = self.num_branches
        elif not num_branches:
            num_branches = len(branches)

        with self.app.app_context():
            db = get_db()
            cur = db.cursor()

            cur.execute('SELECT tree_id FROM tree WHERE tree_id=?', [tree_id])
            res = cur.fetchone()
            if res is None:
                raise Exception('entry with tree-id \'{}\' does not exist'.format(tree_id))

            print('\nUpdating branches with tree-id \'{}\' ...'.format(tree_id))
            updated, inserted = 0, 0
            for i in range(num_branches):
                branch = branches[i]
                index = branch.index
                cur.execute('SELECT * FROM branches WHERE tree_id=? AND "ind"=?', [tree_id, index])
                row = cur.fetchone()
                if row is not None:
                    cur.execute('UPDATE branches SET "depth"=?, "length"=?, "width"=?, "angle"=?, "pos_x"=?, "pos_y"=? '
                                ' WHERE tree_id=? AND "ind"=?', [tree_id, index])
                    updated += 1
                else:
                    cur.execute('INSERT INTO branches (tree_id, ind, depth, length, width, angle, pos_x, pos_y) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?, ?);',
                                [tree_id, index, branch.depth, branch.length, branch.width, branch.angle, branch.pos.x,
                                 branch.pos.y])
                    inserted += 1
            db.commit()
            print('  updated {} entries, inserted {} new branch entries'.format(updated, inserted))


    def save_branches(self, **kwargs):
        tree_id = kwargs.get('tree_id', None)
        full_width = kwargs.get('width', 0)
        full_height = kwargs.get('height', 0)
        tree_name = kwargs.get('tree_name', None)
        # for formatting
        newline = True

        # use self.branches if branches kwarg not provided
        branches, num_branches = kwargs.get('branches', None), kwargs.get('num_branches', None)
        if branches is None:
            branches = self.branches
            num_branches = self.num_branches
        elif not num_branches:
            # branches kwarg provided but no num_branches provided
            raise Exception('num_branches not provided')

        with self.app.app_context():
            db = get_db()
            cur = db.cursor()

            if tree_id is not None:
                cur.execute('SELECT tree_id, tree_name FROM tree WHERE tree_id=?', [tree_id])
                res = cur.fetchone()
                if res is not None:
                    # get tree_name from existing entry, if there is one and no tree_name is provided
                    orig_tree_name = res['tree_name']
                    if not tree_name:
                        tree_name = orig_tree_name

                    # get associated tiles
                    cur.execute('SELECT zoom_id FROM zoom_info WHERE tree_id=?', [tree_id])
                    res_zooms = cur.fetchall()
                    cur.execute('SELECT tiles.zoom_id, tree_id FROM zoom_info INNER JOIN tiles ON '
                                'tiles.zoom_id=zoom_info.zoom_id WHERE zoom_info.tree_id=?', [tree_id])
                    res_tiles = cur.fetchall()

                    print('\nDropping existing entry \'{}\', tree_id={}'.format(orig_tree_name, tree_id))
                    print('  {} zoom level(s), {} total tiles'.format(len(res_zooms), len(res_tiles)))
                    newline = False

                    # entries in zoom_info, tiles .. should be deleted automatically
                    # if PRAGMA foreign_keys=ON
                    cur.execute(r'DELETE FROM branches WHERE "tree_id"=?', [tree_id])
                    cur.execute(r'DELETE FROM tree WHERE "tree_id"=?', [tree_id])
                elif not tree_name:
                    tree_name = _get_default_tree_name()

                cur.execute('INSERT INTO tree (tree_id, tree_name, num_branches, full_width, full_height) '
                            'VALUES (?, ?, ?, ?, ?)', [tree_id, tree_name, num_branches, full_width, full_height])
            else:
                if not tree_name:
                    tree_name = _get_default_tree_name()
                cur.execute('INSERT INTO tree (num_branches, tree_name, full_width, full_height) VALUES (?, ?, ?, ?)',
                            [num_branches, tree_name, full_width, full_height])

            cur.execute('SELECT last_insert_rowid()')
            rowid = cur.fetchone()[0]

            if newline:
                print()
            print('Created new entry \'{}\' tree_id={}'.format(tree_name, rowid))

            self.__output_tree['tree_name'] = tree_name
            self.__output_tree['tree_id'] = tree_id
            self.__add_all_branches(cur, rowid, branches=branches, num_branches=num_branches)
            db.commit()

    def __read_all_branches(self, tree_id: int):
        with self.app.app_context():
            db = get_db()
            cur = db.cursor()

            cur.execute('SELECT tree_name FROM tree WHERE tree_id=?', [tree_id])
            res = cur.fetchone()
            if res is None:
                raise Exception('entry with tree_id={} does not exist'.format(tree_id))
            tree_name = res[0]

            print('Reading branches from tree \'{}\', tree_id={} ...'.format(tree_name, tree_id))

            cur.execute('SELECT * FROM branches LEFT JOIN main.branches_ownership ON '
                        'branches.id = branches_ownership.branch_id WHERE tree_id=? ORDER BY "ind" ASC', [tree_id])
            results = cur.fetchall()

        num_branches = len(results)
        branches = [None for i in range(num_branches)]

        layers, layer = [], -1
        for i in range(num_branches):
            row = results[i]
            depth, length, width = row['depth'], row['length'], row['width']
            angle = row['angle']
            posx, posy = row['pos_x'], row['pos_y']
            text = row['text']

            branches[i] = Branch(i, Vec(posx, posy), depth=depth, length=length, width=width, angle=angle, text=text)
            if depth > layer:
                layers.append(i)
                layer = depth
            elif depth < layer:
                raise Exception('branches not in order')

        print('  branches read: {}\n  layers: {}'.format(num_branches, str(layers).strip('[]')))

        self.__branches = branches
        self.__num_branches = num_branches
        self.__layers = layers
        self.__input_tree['tree_name'] = tree_name
        self.__input_tree['tree_id'] = tree_id

    def __add_all_branches(self, cur: sqlite3.Connection.cursor, tree_id: int, branches=None, num_branches=None):
        if branches is not None:
            size = num_branches
            if size is None:
                raise Exception('num_branches not provided')
        else:
            branches = self.branches
            size = self.num_branches

        print('  adding {} branches ...'.format(size))
        for i in range(size):
            branch = branches[i]
            cur.execute('INSERT INTO branches ("ind", depth, length, width, angle, pos_x, pos_y, tree_id)'
                        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        [i, branch.depth, branch.length, branch.width, branch.angle,
                         branch.pos.x, branch.pos.y, tree_id])

    @property
    def app(self):
        return self.__app

    def _input_tree_dict(self) -> dict:
        return self.__input_tree

    def _output_tree_dict(self) -> dict:
        return self.__output_tree

    def _branches(self):
        branches = self.__branches
        return branches if branches else []

    def _layers(self):
        layers = self.__layers
        return layers if layers else []

    def _num_branches(self) -> int:
        return self.__num_branches


class FileLoader(Loader):
    """Save/load branches/tiles to and from local JSON files"""

    def __init__(self):
        self.__branches = None
        self.__layers = None
        self.__num_branches = 0
        self.__tree_name = ''

        self.__input_tree = dict()
        self.__output_tree = dict()

    def load_branches(self, **kwargs):
        file = kwargs.get('file', None)
        new_tree = file is None
        tree_name = kwargs.get('tree_name', None)

        if new_tree:
            max_depth = kwargs.get('max_depth', 10)
            self.__branches, self.__layers, self.__num_branches = generate_tree(max_depth=max_depth)
            if not tree_name:
                self.__input_tree['tree_name'] = _get_default_tree_name()
            else:
                self.__input_tree['tree_name'] = tree_name
        else:
            self.__read_branches(file)

    def save_branches(self, **kwargs):
        tree_name = kwargs.get('tree_name', _get_default_tree_name())
        fname = kwargs.get('file', 'default_tree')

        # use self.branches if branches kwarg not provided
        branches, num_branches = kwargs.get('branches', None), kwargs.get('num_branches', None)
        if branches is None:
            branches = self.branches
            num_branches = self.num_branches
        elif not num_branches:
            # branches kwarg provided but no num_branches provided
            raise Exception('num_branches not provided')

        stream = create_file(fname, relative=current_app.config['CACHE_DIR'])

        with stream as file:
            head, tail = os.path.split(file.name)
            print('Saving branches to {} ...'.format(tail))
            json.dump(
                {
                    'name': tree_name,
                    'branches': branches[:num_branches]
                }, file, cls=BranchJSONEncoder
            )
        self.__output_tree['tree_name'] = tree_name

    def __read_branches(self, fpath):
        stream = open_file(fpath, relative=current_app.config['CACHE_DIR'])

        with stream as file:
            head, tail = os.path.split(file.name)
            print('Reading branches from {} ...'.format(tail))
            tree = json.load(file, object_hook=_as_obj_hook)

        branches = tree['branches']
        tree_name = tree['name']
        self.__input_tree['tree_name'] = tree_name
        self.__input_tree['file'] = file
        self.__branches = branches
        self.__num_branches = len(branches)

        layers = []
        prev, size = -1, self.__num_branches
        for i in range(size):
            branch = branches[i]
            if prev < branch.depth:
                layers.append(i)
                prev = branch.depth
            elif prev > branch.depth:
                raise Exception('branches not in order')
        self.__layers = layers

        print('Read tree \'{}\' with {:d} branches, {:d} layers :\n   {} ...'
              .format(tree_name, size, len(layers), str(layers)))

        return branches

    def _input_tree_dict(self) -> dict:
        return self.__input_tree

    def _output_tree_dict(self) -> dict:
        return self.__output_tree

    def _branches(self):
        branches = self.__branches
        return branches if branches else []

    def _layers(self):
        layers = self.__layers
        return layers if layers else []

    def _num_branches(self) -> int:
        return self.__num_branches
