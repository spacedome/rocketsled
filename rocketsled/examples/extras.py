from __future__ import unicode_literals, print_function, division

"""
Examples of using extra rocketsled features.

In this task, the calculation and optimization includes a categorical dimension,
a function to fetch extra features (get_z), a custom predictor function, extra 
arguments to the workflow creator, duplicate checking enabled, and a custom 
storage location for the optimization data. Also, a demo of how to use the lpad 
kwarg to store optimization data based on a Firework's LaunchPad object.
"""

from fireworks.core.rocket_launcher import rapidfire
from fireworks import Workflow, Firework, LaunchPad
from rocketsled import OptTask
from rocketsled.examples.tasks import MixedCalculateTask
import random

opt_label = "opt_extras"

# use a wf_creator function with more arguments...
def wf_creator(x, launchpad, my_arg, my_kwarg=1):

    fw1_spec = {'A': x[0], 'B': x[1], 'C': x[2], 'D': x[3], '_x_opt': x}
    fw1_dim = [(1, 2), (1, 2), (1, 2), ("red", "green", "blue")]

    # CalculateTask writes _y_opt field to the spec internally.

    firework1 = Firework([MixedCalculateTask(),
                          OptTask(wf_creator='rocketsled.examples.extras.'
                                             'wf_creator',
                                  dimensions=fw1_dim,
                                  get_z='rocketsled.examples.extras.get_z',
                                  predictor='rocketsled.examples.extras.'
                                            'example_predictor',
                                  max=True,
                                  lpad=launchpad,
                                  random_proba = 0.5,
                                  wf_creator_args=[launchpad, my_arg * 3],
                                  wf_creator_kwargs={'my_kwarg': my_kwarg * 2},
                                  duplicate_check=True,
                                  opt_label=opt_label,
                                  retrain_interval=5)],
                         spec=fw1_spec)
    return Workflow([firework1])


# An optional function which returns extra information 'z' from unique vector 'x'
def get_z(x):
    return x + [x[0] * 2.1, x[2] ** 3.4]

# how an example custom optimization function could be used
# replace the code inside example_predictor with your favorite optimizer

def example_predictor(XZ_explored, Y, x_dims, XZ_unexplored):
    # custom optimizer code goes here
    return random.choice(XZ_unexplored)

def run_workflows():
    TESTDB_NAME = 'rsled'

    # clean up tw database if necessary
    launchpad = LaunchPad(name=TESTDB_NAME)
    launchpad.reset(password=None, require_password=False)

    launchpad.add_wf(wf_creator([1, 1, 2, "red"], launchpad, 3, my_kwarg=1))

    # if n_launches > 24 for this particular example, the search space will be
    # exhausted and OptTask will throw an exception
    rapidfire(launchpad, nlaunches=24, sleep_time=0)

    # tear down database
    # launchpad.connection.drop_database(TESTDB_NAME)


if __name__ == "__main__":
    run_workflows()