from __future__ import unicode_literals, print_function, unicode_literals

"""
This is an example of multiobjective optimization. It is the same as the basic
example, but instead of just returning the sum of the input array, our objective
function, MultiTask2, returns the sum and product of the input array. We want to
maximize both of these quantities.

Best solutions are identified as those having at least one objective value 
better or equal to any other in the set; these points are called "Pareto 
optimal". 

From a user perspective, operation is the same as for single objective 
optimization; however, different acquisition functions are available. Use 
acq=None (default) for a highly exploitative (greedy) algorithm predicting 
Pareto-optimal solutions. Choose acq="maximin" for a more advanced bootstrapping
algorithm for acquisition based on Expected Improvement. 

"""

from fireworks.core.rocket_launcher import rapidfire
from fireworks import Workflow, Firework, LaunchPad
from rocketsled import OptTask
from rocketsled.examples.tasks import MultiTask2, MultiTask6


# a workflow creator function which takes x and returns a workflow based on x
def wf_creator(x):

    spec = {'_x_opt':x}
    X_dim = [(1.0, 5.0), (1.0, 5.0), (1.0, 5.0)]

    # MultiTasks write _y_opt field to the spec internally.
    # MultiTask2 has 2 objectives.
    # MultiTask6, the 6-objective version of MultiTask2, has 6 objectives.
    # Select it by uncommenting it and commenting out MultiTask2.

    firework1 = Firework([
                          MultiTask2(),
                          # MultiTask6(),
                          OptTask(wf_creator='rocketsled.examples.multi.'
                                             'wf_creator',
                                  dimensions=X_dim,
                                  host='localhost',
                                  port=27017,
                                  predictor="GaussianProcessRegressor",
                                  acq="maximin",
                                  opt_label='opt_multi',
                                  name='rsled')],
                          spec=spec)
    return Workflow([firework1])

def run_workflows():
    TESTDB_NAME = 'rsled'
    launchpad = LaunchPad(name=TESTDB_NAME)
    launchpad.reset(password='2018-05-11')
    launchpad.add_wf(wf_creator([5.0, 5.0, 2.0]))
    rapidfire(launchpad, nlaunches=30, sleep_time=0)

    # tear down database
    # launchpad.connection.drop_database(TESTDB_NAME)

if __name__ == "__main__":
    run_workflows()



