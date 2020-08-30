import cobra

from SALib.sample import saltelli

import numpy as np
import scipy
import pickle

try:
    from mpi4py import MPI
except:
    print("MPI not available: disabled")

if __name__ == '__main__':

    comm = MPI.COMM_WORLD
    size = comm.Get_size()
    rank = comm.Get_rank()

    #Recon2.2 -> MODEL1603150001.pkl
    #Recon3DModel -> Recon3DModel_301.pkl

    model = None
    with open('/davide/home/userexternal/vcoelho0/sensitivity/model/MODEL1603150001.pkl',
              'rb') as f:
        model = pickle.load(f)
    '''
    model = cobra.io.read_sbml_model('.xml')
    model.objective = model.reactions.get_by_id('biomass_synthesis')
    '''
    # Broadcast target reactions across tasks.
    if rank == 0:

        # Find inputs names.
        target = []
        for reaction in model.reactions:
            if 'EX_' in reaction.id and reaction.lower_bound < 0:
                target.append(reaction.id)

        print("This model contains:\n" +
              str(len(model.genes)) + " genes\n" +
              str(len(model.reactions)) + " reactions\n" +
              str(len(model.metabolites)) + " metabolites\n" +
              str(len(target)) + " target reactions\n")

    else:
        target = None

    target = comm.bcast(target, root=0)

    # Number of inputs.
    D = len(target)

    # Defining the model inputs.
    problem = {
        'num_vars' : D,
        'names' : target,
        'bounds' : [ [-10, 0] ] * D
    }

    # Number of samples to generate.
    N = 2**15;

    if rank == 0:

        # Return a NumPy matrix containing the model inputs using
        # Saltelli's sampling scheme.
        # Saltelli's scheme extends the Sobol sequence in a way to reduce
        # the error rates in the resulting sensitivity index calculations.
        # If calc_second_order is True
        # the resulting matrix has N * (2 * D + 2) rows
        # otherwise N * (D + 2) rows.

        X = saltelli.sample(problem,
                            N,
                            calc_second_order=True)

        print("The Saltelli sampler generated {0} samples".format(X.shape[0]))

        # Model outputs.
        Y = np.empty(X.shape[0], dtype='float64')

        # Split the NumPy object by the number of available tasks.
        # Even if 'size' does not equally divide the axis.
        # For an array of length l that should be splitted into n sections,
        # it returns l % n sub-arrays of size l//n + 1 and the rest of size l//n.
        chunks = np.array_split(X, size)

        # Send one chunk per slave.
        count = np.empty(0)
        for i in range (1, len(chunks)):
            shape = chunks[i].shape
            comm.send(shape, dest=i, tag=i)
            comm.Send([chunks[i], MPI.DOUBLE], dest=i, tag=i+size)
            count = np.append(count, shape[0])

        # Master chunk.
        chunk = chunks[0]
        shape = chunks[0].shape

        # Add first dimension of master chunk.
        count = np.insert(count, 0, shape[0])

        # Cumulative sum of the elements of 'count'.
        # Insert a 0 value before index 0.
        # Return all elements except the last one.
        displ = np.insert(np.cumsum(count), 0, 0)[0:-1]

    else:
        X = None
        Y = None
        shape = None
        chunk = None
        count = None
        displ = None

    if rank != 0:
        shape = comm.recv(source=0, tag=rank)
        chunk = np.empty(shape, dtype='float64')
        #print("Rank {0} with chunk shape {1}".format(rank, chunk.shape))
        comm.Recv([chunk, MPI.DOUBLE], source=0, tag=rank+size)

    # High Performance Computing (HPC) loop
    names = problem['names']
    get_reaction_by_id = model.reactions.get_by_id
    partial_Y = np.empty(chunk.shape[0], dtype='float64')

    for i, sample in enumerate(chunk):
        for name, value in zip(names, sample):
            get_reaction_by_id(name).lower_bound = value
        partial_Y[i] = model.slim_optimize()

    count = comm.bcast(count, root=0)
    displ = comm.bcast(displ, root=0)

    # Gather NumPy objects (following rank order).
    comm.Gatherv(partial_Y,
                 [Y, count, displ, MPI.DOUBLE],
                 root=0)

    # Global Synchronisation.
    comm.Barrier()

    # Write results.
    if rank == 0:

        with open('Problem.pkl', 'wb') as f:
            pickle.dump(problem, f, pickle.HIGHEST_PROTOCOL)

        with open('Y.pkl', 'wb') as f:
            pickle.dump(Y, f, pickle.HIGHEST_PROTOCOL)
