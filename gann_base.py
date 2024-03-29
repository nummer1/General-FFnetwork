import tensorflow as tf
import numpy as np
import math
import random
import matplotlib.pyplot as PLT
import tflowtools as TFT

# ******* A General Artificial Neural Network ********
# This is the original GANN, which has been improved in the file gann.py

class Gann():
    def __init__(self, dims, cman, afunc, ofunc, cfunc, optimizer, lrate, wrange, vint, mbs, usevsi, showint=None):
        self.layer_sizes = dims  # Sizes of each layer of neurons
        self.caseman = cman
        self.activation_func = afunc
        self.activation_outputs = ofunc
        self.loss_function = cfunc
        self.optimizer_class = optimizer
        self.learning_rate = lrate
        self.weight_range = wrange
        self.show_interval = showint  # Frequency of showing grabbed variables
        self.minibatch_size = mbs
        self.validation_interval = vint
        self.usevsi = usevsi
        self.global_training_step = 0  # Enables coherent data-storage during extra training runs (see runmore).
        self.grabvars = []  # Variables to be monitored (by gann code) during a run.
        self.grabvar_figures = []  # One matplotlib figure for each grabvar
        self.validation_history = []
        self.modules = []
        self.build()

    # Probed variables are to be displayed in the Tensorboard.
    def gen_probe(self, module_index, type, spec):
        self.modules[module_index].gen_probe(type, spec)

    # Grabvars are displayed by my own code, so I have more control over the display format.
    # Each grabvar gets its own matplotlib figure in which to display its value.
    def add_grabvar(self, module_index, type='wgt', add_figure=True):
        self.grabvars.append(self.modules[module_index].getvar(type))
        if add_figure:
            self.grabvar_figures.append(PLT.figure())

    def remove_grabvars(self):
        self.grabvars = []
        self.grabvar_figures = []

    def roundup_probes(self):
        self.probes = tf.summary.merge_all()

    def add_module(self, module): self.modules.append(module)

    def build(self):
        tf.reset_default_graph()  # This is essential for doing multiple runs!!
        num_inputs = self.layer_sizes[0]
        self.input = tf.placeholder(tf.float64, shape=(None, num_inputs), name='Input')
        invar = self.input
        insize = num_inputs
        # Build all of the modules
        for i, outsize in enumerate(self.layer_sizes[1:]):
            gmod = Gannmodule(self, i, invar, insize, outsize, self.activation_func, self.weight_range, self.usevsi)
            invar = gmod.output
            insize = gmod.outsize
        self.output = gmod.output  # Output of last module is output of whole network
        if self.activation_outputs:
            self.output = self.activation_outputs(self.output)
        self.target = tf.placeholder(tf.float64, shape=(None, gmod.outsize), name='Target')
        self.configure_learning()

    # The optimizer knows to gather up all "trainable" variables in the function graph and compute
    # derivatives of the error function with respect to each component of each variable, i.e. each weight
    # of the weight array.
    def configure_learning(self):
        # self.error = tf.reduce_mean(tf.square(self.target - self.output), name='MSE')
        self.error = self.loss_function(self.target, self.output)
        self.predictor = self.output  # Simple prediction runs will request the value of output neurons
        # Defining the training operator
        optimizer = self.optimizer_class(self.learning_rate)
        self.trainer = optimizer.minimize(self.error, name='Backprop')

    def do_training(self, sess, cases, steps, continued=False):
        if not(continued): self.error_history = []
        for i in range(steps):
            error = 0
            step = self.global_training_step + i
            gvars = [self.error] + self.grabvars
            mbs = self.minibatch_size
            minibatch = random.sample(list(cases), mbs)  # randomly selected of size mbs
            inputs = [c[0] for c in minibatch]
            targets = [c[1] for c in minibatch]
            feeder = {self.input: inputs, self.target: targets}
            _, grabvals, _ = self.run_one_step([self.trainer], gvars, self.probes, session=sess,
                        feed_dict=feeder, step=step, show_interval=self.show_interval)
            error += grabvals[0]
            self.error_history.append((step, error))
            self.consider_validation_testing(step, sess)
        self.global_training_step += steps
        TFT.plot_training_history(self.error_history, self.validation_history,
                    xtitle="Step", ytitle="Error", title="", fig=not(continued))

    # bestk = 1 when you're doing a classification task and the targets are one-hot vectors.
    # This will invoke the gen_match_counter error function.
    # Otherwise, when bestk=None, the standard MSE error function is used for testing.

    def do_testing(self, sess, cases, msg='Testing', bestk=None):
        inputs = [c[0] for c in cases]
        targets = [c[1] for c in cases]
        feeder = {self.input: inputs, self.target: targets}
        self.test_func = self.error
        if bestk is not None:
            self.test_func = self.gen_match_counter(self.predictor, [TFT.one_hot_to_int(list(v)) for v in targets], k=bestk)
        testres, grabvals, _ = self.run_one_step(self.test_func, self.grabvars, self.probes,
                    session=sess, feed_dict=feeder, show_interval=None)
        if bestk is None:
            print('%s Set Error = %f ' % (msg, testres))
        else:
            print('%s Set Correct Classifications = %f %%' % (msg, 100*(testres/len(cases))))
        return testres  # self.error uses MSE, so this is a per-case value when bestk=None

    def do_mapping(self):
        self.reopen_current_session()
        sess = self.current_session
        cases = self.caseman.get_mapping_cases()
        results = []
        labels = []
        for i, case in enumerate(cases):
            feeder = {self.input: [case[0]], self.target: [case[1]]}
            self.test_func = self.error
            _, grabvals, _ = self.run_one_step(self.test_func, self.grabvars, self.probes,
                    session=sess, feed_dict=feeder, show_interval=None, display_vars=False)
            results.append(grabvals)
            labels.append(case[1])
        self.close_current_session(view=False)
        return results, labels

    # Logits = tensor, float - [batch_size, NUM_CLASSES].
    # labels: Labels tensor, int32 - [batch_size], with values in range [0, NUM_CLASSES).
    # in_top_k checks whether correct val is in the top k logit outputs.  It returns a vector of shape [batch_size]
    # This returns an OPERATION object that still needs to be RUN to get a count.
    # tf.nn.top_k differs from tf.nn.in_top_k in the way they handle ties.  The former takes the lowest index, while
    # the latter includes them ALL in the "top_k", even if that means having more than k "winners".  This causes
    # problems when ALL outputs are the same value, such as 0, since in_top_k would then signal a match for any
    # target.  Unfortunately, top_k requires a different set of arguments...and is harder to use.

    def gen_match_counter(self, logits, labels, k=1):
        correct = tf.nn.in_top_k(tf.cast(logits, tf.float32), labels, k)  # Return number of correct outputs
        # _, indices1 = tf.nn.top_k(tf.cast(logits, tf.float32), k=k, sorted=False)
        # _, indices2 = tf.nn.top_k(tf.cast(labels, tf.float32), k=k, sorted=False)
        # correct = tf.equal(indices1, indices2)
        return tf.reduce_sum(tf.cast(correct, tf.int32))

    def training_session(self, steps, sess=None, dir="probeview", continued=False):
        session = sess if sess else TFT.gen_initialized_session(dir=dir)
        self.current_session = session
        self.roundup_probes()  # this call must come AFTER the session is created, else graph is not in tensorboard.
        self.do_training(session, self.caseman.get_training_cases(), steps, continued=continued)

    def testing_session(self, sess, bestk=None):
        cases = self.caseman.get_testing_cases()
        if len(cases) > 0:
            self.do_testing(sess, cases, msg='Final Testing', bestk=bestk)

    def consider_validation_testing(self, step, sess):
        if self.validation_interval and (step % self.validation_interval == 0):
            cases = self.caseman.get_validation_cases()
            if len(cases) > 0:
                error = self.do_testing(sess, cases, msg='Validation Testing')
                self.validation_history.append((step, error))

    # Do testing (i.e. calc error without learning) on the training set.
    def test_on_trains(self, sess, bestk=None):
        self.do_testing(sess, self.caseman.get_training_cases(), msg='Total Training', bestk=bestk)

    # Similar to the "quickrun" functions used earlier.
    def run_one_step(self, operators, grabbed_vars=None, probed_vars=None, dir='probeview',
                    session=None, feed_dict=None, step=1, show_interval=1, display_vars=True):
        sess = session if session else TFT.gen_initialized_session(dir=dir)
        if probed_vars is not None:
            results = sess.run([operators, grabbed_vars, probed_vars], feed_dict=feed_dict)
            sess.probe_stream.add_summary(results[2], global_step=step)
        else:
            results = sess.run([operators, grabbed_vars], feed_dict=feed_dict)
        if show_interval and (step % show_interval == 0) and display_vars:
            self.display_grabvars(results[1], grabbed_vars, step=step)
        return results[0], results[1], sess

    def display_grabvars(self, grabbed_vals, grabbed_vars,step=1):
        names = [x.name for x in grabbed_vars]
        msg = "Grabbed Variables at Step " + str(step)
        print("\n" + msg, end="\n")
        fig_index = 0
        for i, v in enumerate(grabbed_vals):
            if names: print("   " + names[i] + " = ", end="\n")
            if type(v) == np.ndarray:  # and len(v.shape) > 1:  # If v is a matrix, use hinton plotting
                TFT.hinton_plot(v, fig=self.grabvar_figures[fig_index], title= names[i]+ ' at step ' + str(step))
                fig_index += 1
            else:
                print(v, end="\n\n")

    def run(self, steps=100, sess=None, continued=False, bestk=None):
        PLT.ion()
        self.training_session(steps, sess=sess, continued=continued)
        self.test_on_trains(sess=self.current_session, bestk=bestk)
        self.testing_session(sess=self.current_session, bestk=bestk)
        self.close_current_session(view=False)
        PLT.ioff()

    # After a run is complete, runmore allows us to do additional training on the network, picking up where we
    # left off after the last call to run (or runmore).  Use of the "continued" parameter (along with
    # global_training_step) allows easy updating of the error graph to account for the additional run(s).

    def runmore(self, steps=100, bestk=None):
        self.reopen_current_session()
        self.run(steps, sess=self.current_session, continued=True, bestk=bestk)

    #   ******* Saving GANN Parameters (weights and biases) *******************
    # This is useful when you want to use "runmore" to do additional training on a network.
    # spath should have at least one directory (e.g. netsaver), which you will need to create ahead of time.
    # This is also useful for situations where you want to first train the network, then save its parameters
    # (i.e. weights and biases), and then run the trained network on a set of test cases where you may choose to
    # monitor the network's activity (via grabvars, probes, etc) in a different way than you monitored during
    # training.

    def gen_state_saver(self, mode='auto'):
        if mode == 'manual':
            state_vars = []
            for m in self.modules:
                 state_vars = state_vars + m.get_all_state_vars()  # E.g. get wgts and biases for a module
            self.state_saver = tf.train.Saver(state_vars)
        else:
            with self.function_graph.as_default():
                 self.state_saver = tf.train.Saver()

    def save_session_params(self, spath='netsaver/my_saved_session', sess=None, step=0):
        session = sess if sess else self.current_session
        state_vars = []
        for m in self.modules:
            vars = [m.getvar('wgt'), m.getvar('bias')]
            state_vars = state_vars + vars
        self.state_saver = tf.train.Saver(state_vars)
        self.saved_state_path = self.state_saver.save(session, spath, global_step=step)

    def reopen_current_session(self):
        self.current_session = TFT.copy_session(self.current_session)  # Open a new session with same tensorboard stuff
        self.current_session.run(tf.global_variables_initializer())
        self.restore_session_params()  # Reload old weights and biases to continued from where we last left off

    def restore_session_params(self, path=None, sess=None):
        spath = path if path else self.saved_state_path
        session = sess if sess else self.current_session
        self.state_saver.restore(session, spath)

    def close_current_session(self,view=True):
        self.save_session_params(sess=self.current_session)
        TFT.close_session(self.current_session, view=view)


# A general ann module = a layer of neurons (the output) plus its incoming weights and biases.
class Gannmodule():
    def __init__(self, ann, index, invariable, insize, outsize, afunc, wrange, usevsi):
        self.ann = ann
        self.index = index
        self.input = invariable  # Either the gann's input variable or the upstream module's output
        self.insize = insize  # Number of neurons feeding into this module
        self.outsize = outsize  # Number of neurons in this module
        self.activation_func = afunc
        self.wrange = wrange
        self.usevsi = usevsi
        self.name = "Module-"+str(self.index)
        self.build()

    def build(self):
        mona = self.name
        n = self.outsize
        if self.usevsi:
            initializer = tf.contrib.layers.variance_scaling_initializer(mode='FAN_IN', dtype=tf.float64)
            self.weights = tf.Variable(initializer(shape=(self.insize, n)),
                        name=mona+'-wgt',trainable=True)
        else:
            self.weights = tf.Variable(np.random.uniform(self.wrange[0], self.wrange[1], size=(self.insize, n)),
                        name=mona+'-wgt',trainable=True)  # True = default for trainable anyway
        self.biases = tf.Variable(np.random.uniform(self.wrange[0], self.wrange[1], size=n),
                    name=mona+'-bias', trainable=True)  # First bias vector
        self.output = self.activation_func(tf.matmul(self.input, self.weights) + self.biases, name=mona+'-out')
        self.ann.add_module(self)

    def getvar(self, type):  # type = (in,out,wgt,bias)
        return {'in': self.input, 'out': self.output, 'wgt': self.weights, 'bias': self.biases}[type]

    # spec, a list, can contain one or more of (avg,max,min,hist); type = (in, out, wgt, bias)
    def gen_probe(self, type, spec):
        var = self.getvar(type)
        base = self.name + '_' + type
        with tf.name_scope('probe_'):
            if ('avg' in spec) or ('stdev' in spec):
                avg = tf.reduce_mean(var)
            if 'avg' in spec:
                tf.summary.scalar(base + '/avg/', avg)
            if 'max' in spec:
                tf.summary.scalar(base + '/max/', tf.reduce_max(var))
            if 'min' in spec:
                tf.summary.scalar(base + '/min/', tf.reduce_min(var))
            if 'hist' in spec:
                tf.summary.histogram(base + '/hist/', var)

# *********** CASE MANAGER ********
# This is a simple class for organizing the cases (training, validation and test) for a
# a machine-learning system

class Caseman():
    def __init__(self, cases, vfrac, tfrac, casefrac, mapsep):
        self.cases = cases
        self.mapsep = mapsep
        self.validation_fraction = vfrac * casefrac
        self.test_fraction = tfrac * casefrac
        self.training_fraction = (1 - (vfrac + tfrac)) * casefrac
        self.organize_cases()

    def organize_cases(self):
        ca = np.array(self.cases)
        np.random.shuffle(ca) # Randomly shuffle all cases
        separator1 = round(len(self.cases) * self.training_fraction)
        separator2 = separator1 + round(len(self.cases) * self.validation_fraction)
        self.training_cases = ca[0:separator1]
        self.validation_cases = ca[separator1:separator2]
        self.testing_cases = ca[separator2:]
        np.random.shuffle(ca)
        self.mapping_cases = ca[0:min(self.mapsep, len(ca))]

    def get_training_cases(self): return self.training_cases
    def get_validation_cases(self): return self.validation_cases
    def get_testing_cases(self): return self.testing_cases
    def get_mapping_cases(self): return self.mapping_cases


#   ****  MAIN functions ****

# After running this, open a Tensorboard (Go to localhost:6006 in your Chrome Browser) and check the
# 'scalar', 'distribution' and 'histogram' menu options to view the probed variables.
def autoex(steps=300,nbits=4,lrate=0.03,showint=100,mbs=None,vfrac=0.1,tfrac=0.1,vint=100,sm=False,bestk=None):
    size = 2**nbits
    mbs = mbs if mbs else size
    case_generator = (lambda: TFT.gen_all_one_hot_cases(2**nbits))
    cman = Caseman(cfunc=case_generator,vfrac=vfrac,tfrac=tfrac)
    ann = Gann(dims=[size,nbits,size],cman=cman,afunc=tf.nn.relu,lrate=lrate,showint=showint,mbs=mbs,vint=vint,softmax=sm)
    ann.gen_probe(0,'wgt',('hist','avg'))  # Plot a histogram and avg of the incoming weights to module 0.
    ann.gen_probe(1,'out',('avg','max'))  # Plot average and max value of module 1's output vector
    ann.add_grabvar(0,'wgt') # Add a grabvar (to be displayed in its own matplotlib window).
    ann.run(steps, bestk=bestk)
    ann.runmore(steps*2,bestk=bestk)
    TFT.fireup_tensorboard('probeview')
    return ann

def countex(steps=5000,nbits=15,ncases=500,lrate=0.5,showint=500,mbs=20,vfrac=0.1,tfrac=0.1,vint=200,sm=True,bestk=1):
    case_generator = (lambda: TFT.gen_vector_count_cases(ncases,nbits))
    cman = Caseman(cfunc=case_generator, vfrac=vfrac, tfrac=tfrac)
    ann = Gann(dims=[nbits, nbits*3, nbits+1], cman=cman, lrate=lrate, showint=showint, mbs=mbs, vint=vint, softmax=sm)
    ann.run(steps,bestk=bestk)
    TFT.fireup_tensorboard('probeview')
    return ann
