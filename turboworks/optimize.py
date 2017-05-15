"""
The FireTask for running automatic optimization loops are contained in this module.
"""

import sys
from itertools import product
from os import getpid
from fireworks.utilities.fw_utilities import explicit_serialize
from fireworks.core.firework import FireTaskBase
from fireworks import FWAction, LaunchPad
from pymongo import MongoClient, ReturnDocument
from time import sleep
from numpy import sctypes
import random
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.svm import SVR
from sklearn.preprocessing import LabelBinarizer
import numpy as np

__author__ = "Alexander Dunn"
__version__ = "0.1"
__email__ = "ardunn@lbl.gov"


@explicit_serialize
class OptTask(FireTaskBase):
    """
    A FireTask for running optimization loops automatically.

    OptTask takes in and stores a vector 'x' which uniquely defines the input space and a scalar 'yi' which is the
    scoring metric. OptTask produces a new x vector to minimize y using information from all x vectors (X) and y
    scalars (Y). Additionally, a z vector of extra features can be used by OptTask to better optimize, although new
    values of z will be discarded.

    Required args:
        wf_creator (function): returns a workflow based on a unique vector, x.
        dimensions ([tuple]): each 2-tuple in the list defines the search space in (low, high) format.
            For categorical dimensions, includes all possible categories as a list.
            Example: dimensions = dim = [(1,100), (9.293, 18.2838), ("red", "blue", "green")].
    Optional args:
        get_z (string): the fully-qualified name of a function which, given a x vector, returns another vector z which
            provides extra information to the machine learner. The features defined in z are not used to run the
            workflow creator.
            Examples: 
                get_z = 'my_module.my_fun'
                get_z = '/path/to/folder/containing/my_package.my_module.my_fun'
        predictor (string): names a function which given a list of inputs, a list of outputs, and a dimensions space,
            can return a new optimized input vector. Can specify either a skopt function or a custom function.
            Example: predictor = 'my_module.my_predictor'
        max (bool): Makes optimization tend toward maximum values instead of minimum ones.
        wf_creator_args (list): details the positional args to be passed to the wf_creator function alongsize the z
            vector
        wf_creator_kwargs (dict): details the kwargs to be passed to the wf_creator function alongside the z vector
        duplicate_check (bool): If True, checks for duplicate guesss in discrete, finite spaces. (NOT currently 100%
            working with concurrent workflows). Default is no duplicate check.
        host (string): The name of the MongoDB host where the optimization data will be stored. The default is
            'localhost'.
        port (int): The number of the MongoDB port where the optimization data will be stored. The default is 27017.
        name (string): The name of the MongoDB database where the optimization data will be stored.
        lpad (LaunchPad): A Fireworks LaunchPad object.
        opt_label (string): Names the collection of that the particular optinization's data will be stored in. Multiple
            collections correspond to multiple independent optimization.
        retrain_interval (int): The number of iterations to wait before retraining the expensive model. On iterations
            where the model is not trained, a random guess is used. 
        verify_z (bool): if True, will verify that the predicted x guess is not based on a bad z prediction. 
    """
    _fw_name = "OptTask"
    required_params = ['wf_creator', 'dimensions']
    optional_params = ['get_z', 'predictor', 'max', 'wf_creator_args', 'wf_creator_kwargs', 'duplicate_check',
                       'host', 'port', 'name', 'opt_label', 'lpad', 'retrain_interval', 'n_points', 'predictor_args',
                       'predictor_kwargs', 'encode_categorical']

    # todo: update documentation

    def run_task(self, fw_spec):
        """
        FireTask for running an optimization loop.

        Args:
            fw_spec (dict): the firetask spec. Must contain a '_y_opt' key with a float type field and must contain
                a '_x_opt' key containing a vector uniquely defining the search space.

        Returns:
            (FWAction)
        """

        # the pid identifies the process during parallel duplicate checking
        pid = getpid()
        sleeptime = .001
        max_runs = 10000
        max_resets = 5
        self._setup_db(fw_spec)

        for run in range(max_resets * max_runs):
            manager_docs = self.collection.find(self.manager_format)

            if manager_docs.count() == 0:
                self.collection.insert_one({'lock': pid, 'queue': []})
            elif manager_docs.count() == 1:

                try:
                    manager = self.collection.find_one(self.manager_format)
                    manager_id = manager['_id']
                    lock = manager['lock']

                except TypeError:
                    continue

                if lock is None:
                    self.collection.find_one_and_update({'_id': manager_id}, {'$set': {'lock': pid}})

                elif lock != pid:
                    if pid not in manager['queue']:

                        # avoid bootup problems if manager docs are being deleted concurrently with this check
                        try:
                            new_queue = self.collection.find_one({'_id': manager_id})['queue']
                            new_queue.append(pid)
                            self.collection.find_one_and_update({'_id': manager_id}, {'$set': {'queue': new_queue}})

                        except TypeError:
                            continue

                    else:
                        sleep(sleeptime)

                elif lock == pid:

                    x = fw_spec['_x_opt']
                    yi = fw_spec['_y_opt']
                    self.dtypes = Dtypes()

                    # type safety for dimensions to avoid cryptic skopt errors
                    x_dims = [tuple(dim) for dim in self['dimensions']]

                    # fetch additional attributes for constructing machine learning model by calling get_z, if it exists
                    self.get_z = self._deserialize(self['get_z']) if 'get_z' in self else lambda input_vector: []
                    z = self.get_z(x)

                    # compound the additional attributes to the unique vectors to feed into machine learning
                    XZ = [x + z]
                    y = [yi]
                    for doc in self.collection.find(self.opt_format, projection={'x': 1, 'yi': 1, 'z': 1}):
                        XZ.append(doc['x'] + doc['z'])
                        y.append(doc['yi'])

                    # todo: spamming get_z with guesses can be prevented, but it would require this entire section be
                    # todo: (cont.) inside the pid lock loop, hence locking the db for each training
                    n_points = self['n_points'] if 'n_points' in self else 10000
                    X_space = self._discretize_space(x_dims, n_points=n_points, discrete_floats=True, n_floats=100)
                    X_unexplored = [xi for xi in X_space if self.collection.find({'x': xi}).count() == 0 and xi != x]

                    if not X_unexplored: raise Exception("The discrete space has been searched exhaustively.")

                    XZ_unexplored = [xi + self.get_z(xi) for xi in X_unexplored]
                    xz_dims = x_dims + self._z_dims(XZ_unexplored, len(x))

                    # change y vector if maximum is desired instead of minimum
                    max_on = self['max'] if 'max' in self else False
                    y = [-1 * yi if max_on else yi for yi in y]

                    # run machine learner on Z and X features
                    retrain_interval = self['retrain_interval'] if 'retrain_interval' in self else 1
                    encode_categorical = self['encode_categorical'] if 'encode_categorical' in self else False

                    self.predictors = ['RandomForestRegressor',
                                       'GaussianProcessRegressor',
                                       'LinearRegression',
                                       'MLPRegressor',
                                       'SVR']

                    if self.collection.find(self.opt_format).count() % retrain_interval == 0:
                        predictor = 'RandomForestRegressor' if 'predictor' not in self else self['predictor']
                    else:
                        predictor = 'random_guess'

                    pred_args = self['predictor_args'] if 'predictor_args' in self else []
                    pred_kwargs = self['predictor_kwargs'] if 'predictor_kwargs' in self else {}

                    if predictor in self.predictors:

                        if predictor == 'RandomForestRegressor':
                            model = RandomForestRegressor
                        elif predictor == 'GaussianProcessRegressor':
                            model = GaussianProcessRegressor
                        elif predictor == 'LinearRegression':
                            model = LinearRegression
                        elif predictor == 'MLPRegressor':
                            model = MLPRegressor
                        elif predictor == 'SVR':
                            model = SVR

                        XZ = self._preprocess(XZ, xz_dims)
                        XZ_unexplored = self._preprocess(XZ_unexplored, xz_dims)
                        xz_onehot = self._predict(XZ, y, XZ_unexplored, model(*pred_args, **pred_kwargs))
                        xz_new = self._postprocess(xz_onehot, xz_dims)

                    elif predictor == 'random_guess':
                        x_new = random_guess(x_dims, self.dtypes)
                        xz_new = x_new + self.get_z(x_new)

                    else:
                        if encode_categorical:
                            XZ = self._preprocess(XZ, xz_dims)
                            XZ_unexplored = self._preprocess(XZ_unexplored, xz_dims)

                        try:
                            predictor_fun = self._deserialize(predictor)
                            xz_new = predictor_fun(XZ, y, XZ_unexplored, *pred_args, **pred_kwargs)

                        except Exception as E:
                            raise ValueError(
                                "The custom predictor function {} did not call correctly! \n {}".format(predictor, E))

                    # separate 'predicted' z features from the new x vector
                    x_new, z_new = xz_new[:len(x)], xz_new[len(x):]

                    # duplicate checking for custom optimizer functions
                    if 'duplicate_check' in self and predictor not in self.predictors:
                        if self['duplicate_check']:
                            if self._is_discrete(x_dims):
                                x_new = random.choice(X_unexplored)
                                z_new = self.get_z(x_new)

                    # make sure a process has not timed out and changed the lock pid while this process
                    # is computing the next guess
                    if self.collection.find_one(self.manager_format)['lock'] != pid:
                        continue
                    else:
                        opt_id = self._store({'z': z, 'yi': yi, 'x': x, 'z_new': z_new, 'x_new': x_new})

                    queue = self.collection.find_one({'_id': manager_id})['queue']
                    if not queue:
                        self.collection.find_one_and_update({'_id': manager_id}, {'$set': {'lock': None}})
                    else:
                        new_lock, new_queue = queue[0], queue[1:]
                        self.collection.find_one_and_update({'_id': manager_id},
                                                            {'$set': {'lock': new_lock, 'queue': new_queue}})

                    wf_creator = self._deserialize(self['wf_creator'])

                    wf_creator_args = self['wf_creator_args'] if 'wf_creator_args' in self else []
                    if not isinstance(wf_creator_args, list) or isinstance(wf_creator_args, tuple):
                        raise TypeError("wf_creator_args should be a list/tuple of positional arguments")

                    wf_creator_kwargs = self['wf_creator_kwargs'] if 'wf_creator_kwargs' in self else {}
                    if not isinstance(wf_creator_kwargs, dict):
                        raise TypeError("wf_creator_kwargs should be a dictonary of keyword arguments.")

                    return FWAction(additions=wf_creator(x_new, *wf_creator_args, **wf_creator_kwargs),
                                    update_spec={'optimization_id': opt_id})

            else:
                self.collection.delete_one(self.manager_format)

            if run in [max_runs*k for k in range(1, max_resets)]:
                self.collection.find_one_and_update(self.manager_format, {'$set': {'lock': None, 'queue': []}})

            elif run == max_runs*max_resets:
                raise Exception("The manager is still stuck after resetting. Make sure no stalled processes are"
                            " in the queue.")

    def _setup_db(self, fw_spec):
        """
        Sets up a MongoDB database for storing optimization data.

        Args:
            fw_spec (dict): The spec of the Firework which contains this Firetask.

        Returns:
            None
        """

        opt_label = self['opt_label'] if 'opt_label' in self else 'opt_default'
        db_reqs = ('host', 'port', 'name')
        db_defined = [req in self for req in db_reqs]

        if all(db_defined):
            host, port, name = [self[k] for k in db_reqs]

        elif any(db_defined):
            raise AttributeError("Host, port, and name must all be specified!")

        elif 'lpad' in self:
            lpad = self['lpad']
            host, port, name = [lpad[req] for req in db_reqs]

        elif '_add_launchpad_and_fw_id' in fw_spec:
            if fw_spec['_add_launchpad_and_fw_id']:
                try:
                    host, port, name = [getattr(self.launchpad, req) for req in db_reqs]

                except AttributeError:
                    # launchpad tried to get attributes of a multiprocessing proxy object.
                    raise Exception("_add_launchpad_and_fw_id is currently working with parallel workflows.")

        else:
            try:
                host, port, name = [getattr(LaunchPad.auto_load(), req) for req in db_reqs]

            except AttributeError:
                # auto_load did not return any launchpad object, so nothing was defined.
                raise AttributeError("The optimization database must be specified explicitly (with host, port, and"
                                 " name), with Launchpad object (lpad), by setting _add_launchpad_and_fw_id to True in"
                                 " the fw_spec, or by defining LAUNCHPAD_LOC in fw_config.py for LaunchPad.auto_load()")

        mongo = MongoClient(host, port)
        db = getattr(mongo, name)
        self.collection = getattr(db, opt_label)

        self.opt_format = {'x': {'$exists': 1}, 'yi': {'$exists': 1}, 'z': {'$exists': 1}}
        self.manager_format = {'lock': {'$exists': 1}, 'queue': {'$exists': 1}}

    def _store(self, spec):
        """
        Stores and updates turboworks database files.

        Args:
            spec (dict): a turboworks-generated spec (or subset of a spec) to be stored in the turboworks db.

        Returns:
            (ObjectId) the PyMongo BSON id object for the document inserted/updated.
        """

        if 'duplicate_check' in self:
            if self['duplicate_check']:
                # prevents errors when initial guesses are already in the database
                x = spec['x']
                new_doc = self.collection.find_one_and_replace({'x': x},
                                                               spec,
                                                               upsert=True,
                                                               return_document=ReturnDocument.AFTER)
                return new_doc['_id']
        else:
            return self.collection.insert_one(spec).inserted_id

    def _deserialize(self, fun):
        """
        Takes a fireworks serialzed function handle and maps it to a function object.

        Args:
            fun (string): a 'module.function' or '/path/to/mod.func' style string specifying the function

        Returns:
            (function) The function object defined by fun
        """
        # todo: merge with PyTask's deserialize code, move to fw utils

        toks = fun.rsplit(".", 1)
        modname, funcname = toks

        if "/" in toks[0]:
            path, modname = toks[0].rsplit("/", 1)
            sys.path.append(path)

        mod = __import__(str(modname), globals(), locals(), fromlist=[str(funcname)])
        return getattr(mod, funcname)

    def _is_discrete(self, dims):
        """
        Checks if the search space is totally discrete.

        Args:
            dims ([tuple]): dimensions of the search space

        Returns:
            (bool) whether the search space is totally discrete.
        """

        for dim in dims:
            if type(dim[0]) not in self.dtypes.discrete or type(dim[1]) not in self.dtypes.discrete:
                return False
        return True

    def _discretize_space(self, dims, n_points = None, discrete_floats=False, n_floats=100):
        """
        Calculates a list of n_points possible entries of a discrete space from the dimensions of that space. 

        Args:
            dims ([tuple]): list of dimensions of the search space. Individual dimensions should be in (higher, lower)
                form if integers, and should be a comprehensive list if categorical.

        Returns:
            ([list]) all possible combinations inside the discrete space
        """

        total_dimspace = []

        for dim in dims:
            lower = dim[0]
            upper = dim[1]

            if type(lower) in self.dtypes.ints:
                # Then the dimension is of the form (lower, upper)
                dimspace = list(range(lower, upper + 1))
            elif type(lower) in self.dtypes.floats:
                if discrete_floats:
                    dimspace = [random.uniform(lower, upper) for i in range(n_floats)]
                else:
                    raise ValueError("The dimension is a float. The dimension space is infinite.")
            else:  # The dimension is a discrete finite string list
                dimspace = dim
            total_dimspace.append(dimspace)

        space = [[x] for x in total_dimspace[0]] if len(dims) == 1 else [list(x) for x in product(*total_dimspace)]

        if not n_points:
            return space
        else:
            n_points = len(space) if n_points > len(space) else n_points
            return random.sample(space, n_points)

    def _predict(self, X, y, space, model, minimize=True):
        """
        Scikit-learn compatible model for stepwise optimization. It uses a regressive predictor evaluated on all possible 
        remaining points in a discrete space. OptTask Z and X are abstracted.

        Args:
            X ([list]): List of vectors containing input training data.
            y (list): List of scalars containing output training data.
            space ([list]): List of vectors containing all possible inputs. Should be preprocessed before being passed to
                predictor function.
            model (sklearn model): The regressor used for predicting the next best guess.
            n_points (int): The number of points in space to predict over.
            minimize (bool): Makes predictor return the guess which maximizes the predicted objective function output.
                Else maximizes the predicted objective function output.  

        Returns:
            (list) A vector which is predicted to minimize (or maximize) the objective function. This vector contains 
                extra 'z' features which will need to be discarded in postprocessing. However, 'x' and 'z' information
                is guaranteed to match. 

        """
        model.fit(X, y)
        values = model.predict(space).tolist()
        evaluator = min if minimize else max
        i = values.index(evaluator(values))
        return space[i]

    def _preprocess(self, X, dims):

        self._n_cats = 0
        self._bin_info = []

        for i, dim in enumerate(dims):
            if type(dim[0]) in self.dtypes.others:

                cats = [0] * len(X)
                for j, x in enumerate(X):
                    cats[j] = x[i - self._n_cats]

                forward_map = {k: v for v, k in enumerate(dim)}
                inverse_map = {v: k for k, v in forward_map.items()}

                lb = LabelBinarizer()
                lb.fit([forward_map[v] for v in dim])
                binary = lb.transform([forward_map[v] for v in cats])

                for j, x in enumerate(X):
                    del (x[i - self._n_cats])
                    x += list(binary[j])

                dim_info = {'lb': lb, 'inverse_map': inverse_map, 'binary_len': len(binary[0])}
                self._bin_info.append(dim_info)
                self._n_cats += 1

        return X

    def _postprocess(self, new_x, dims):

        original_len = len(dims)
        static_len = original_len - self._n_cats
        exported_new_x = []
        cat_index = 0
        tot_bin_len = 0

        for i, dim in enumerate(dims):
            if type(dim[0]) in self.dtypes.others:
                dim_info = self._bin_info[cat_index]

                binary_len = dim_info['binary_len']
                lb = dim_info['lb']
                inverse_map = dim_info['inverse_map']

                start = static_len + tot_bin_len
                end = start + binary_len
                binary = new_x[start:end]

                int_value = lb.inverse_transform(np.asarray([binary]))[0]
                cat_value = inverse_map[int_value]
                exported_new_x.append(cat_value)

                cat_index += 1
                tot_bin_len += binary_len

            else:
                exported_new_x.append(new_x[i - cat_index])

        return exported_new_x

    def _z_dims(self, XZ_unexplored, x_length):
        """
        Prepare dims to use in preprocessing for categorical dimensions
        
        Returns:
            ([tuple]) a list of dimensions
        """

        Z_unexplored = [z[x_length:] for z in XZ_unexplored]
        Z_explored = [doc['z'] for doc in self.collection.find(self.opt_format)]
        Z = Z_explored + Z_unexplored

        dims = [(z, z) for z in Z[0]]

        for i, dim in enumerate(dims):
            cat_values = []
            for z in Z:
                if type(z[i]) in self.dtypes.others:
                    # the dimension is categorical
                    if z[i] not in cat_values:
                        cat_values.append(z[i])
                        dims[i] = cat_values
        return dims


class Dtypes(object):
    """
    Defines the datatypes available for optimization.
    """

    def __init__(self):
        d = sctypes
        self.ints = d['int'] + d['uint'] + [int]
        self.floats = d['float'] + [float]
        self.reals = self.ints + self.floats
        self.complex = d['complex']
        self.numbers = self.reals + self.complex
        self.others = d['others']
        self.discrete = self.ints + self.others
        self.all = self.numbers + self.others


def random_guess(dimensions, dtypes=Dtypes()):
    """
    Returns random new inputs based on the dimensions of the search space.
    It works with float, integer, and categorical types

    Args:
        dimensions ([tuple]): defines the dimensions of each parameter
            example: [(1,50),(-18.939,22.435),["red", "green" , "blue", "orange"]]

    Returns:
        random_vector (list): randomly chosen next parameters in the search space
            example: [12, 1.9383, "green"]
    """

    random_vector = []

    for dimset in dimensions:
        upper = dimset[1]
        lower = dimset[0]
        if type(lower) in dtypes.ints:
            new_param = random.randint(lower, upper)
            random_vector.append(new_param)
        elif type(lower) in dtypes.floats:
            new_param = random.uniform(lower, upper)
            random_vector.append(new_param)
        elif type(lower) in dtypes.others:
            domain_size = len(dimset)-1
            new_param = random.randint(0, domain_size)
            random_vector.append(dimset[new_param])
        else:
            raise TypeError("The type {} is not supported by dummy opt as a categorical or "
                            "numerical type".format(type(upper)))

    return random_vector

