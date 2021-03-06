'''
Sciris app to run the web interface.
'''

#%% Housekeeping

# Key imports
import os
import sys
import json
import base64
import tempfile
import traceback
import numpy as np
import sciris as sc
import covasim as cv
import shutil as sh
from pathlib import Path
import plotly.figure_factory as ff

# Check requirements, and if met, import scirisweb
cv.requirements.check_scirisweb(die=True)
import scirisweb as sw

# Create the app
app = sw.ScirisApp(__name__, name="Covasim")
flask_app = app.flask_app


# Set defaults
max_pop  = 20e3   # Maximum population size
max_days = 180    # Maximum number of days
max_time = 10     # Maximum of seconds for a run
die      = False  # Whether or not to raise exceptions instead of continuing
bgcolor  = '#eee' # Background color for app
plotbg   = '#dde'


#%% Define the API helper functions

@app.route('/healthcheck')
def healthcheck():
    ''' Check that the server is up '''
    return sw.robustjsonify({'status':'ok'})


def log_err(message, ex):
    ''' Compile error messages to send to the frontend '''
    tex = traceback.TracebackException.from_exception(ex)
    output = {
        "message": message,
        "exception": ''.join(traceback.format_exception(tex.exc_type, tex, tex.exc_traceback))
    }
    sc.pp(output)
    return output


@app.register_RPC()
def get_defaults(region=None, merge=False, die=die):
    ''' Get parameter defaults '''

    if region is None:
        region = 'Example'

    regions = {
        'pop_scale': {
            'Example': 1,
        },
        'pop_size': {
            'Example': 10000,
        },
        'pop_infected': {
            'Example': 100,
        },
    }

    sim_pars = {}
    sim_pars['pop_scale']    = dict(best=1,     min=1, max=1e6,      name='Population scale factor',    tip='Multiplier for results (to approximate large populations)')
    sim_pars['pop_size']     = dict(best=10000, min=1, max=max_pop,  name='Population size',            tip='Number of agents simulated in the model')
    sim_pars['pop_infected'] = dict(best=10,    min=1, max=max_pop,  name='Initial infections',         tip='Number of initial seed infections in the model')
    sim_pars['rand_seed']    = dict(best=0,     min=0, max=100,      name='Random seed',                tip='Random number seed (set to 0 for different results each time)')
    sim_pars['n_days']       = dict(best=90,    min=1, max=max_days, name="Simulation Duration",        tip='Total duration (in days) of the simulation')

    epi_pars = {}
    epi_pars['beta']          = dict(best=0.015, min=0.0, max=0.2, name='Beta (infectiousness)',         tip ='Probability of infection per contact per day')
    epi_pars['contacts']      = dict(best=20,    min=0.0, max=50,  name='Number of contacts',            tip ='Average number of people each person is in contact with each day')
    epi_pars['web_exp2inf']   = dict(best=4.0,   min=1.0, max=30,  name='Time to infectiousness (days)', tip ='Average number of days between exposure and being infectious')
    epi_pars['web_inf2sym']   = dict(best=1.0,   min=1.0, max=30,  name='Asymptomatic period (days)',    tip ='Average number of days between exposure and developing symptoms')
    epi_pars['web_dur']       = dict(best=10.0,  min=1.0, max=30,  name='Infection duration (days)',     tip ='Average number of days between infection and recovery (viral shedding period)')
    epi_pars['web_timetodie'] = dict(best=22.0,  min=1.0, max=60,  name='Time until death (days)',       tip ='Average number of days between infection and death')
    epi_pars['web_cfr']       = dict(best=0.02,  min=0.0, max=1.0, name='Case fatality rate',            tip ='Proportion of people who become infected who die')

    for parkey,valuedict in regions.items():
        sim_pars[parkey]['best'] = valuedict['Example'] # NB, needs to be refactored
    if merge:
        output = {**sim_pars, **epi_pars}
    else:
        output = {'sim_pars': sim_pars, 'epi_pars': epi_pars}

    return output


@app.register_RPC()
def get_version():
    ''' Get the version '''
    output = f'Version {cv.__version__} ({cv.__versiondate__})'
    return output


@app.register_RPC()
def get_licenses():
    cwd = Path(__file__).parent
    repo = cwd.joinpath('../..')
    license = repo.joinpath('LICENSE').read_text(encoding='utf-8')
    notice = repo.joinpath('licenses/NOTICE').read_text(encoding='utf-8')

    return {
        'license': license,
        'notice': notice
    }

@app.register_RPC()
def get_location_options():
    ''' Get the list of options for the location select '''
    json1 = cv.data.country_age_data.get()
    json2 = cv.data.state_age_data.get()
    locations = list(json1.keys()) + list(json2.keys())
    return locations


@app.register_RPC(call_type='upload')
def upload_pars(fname):
    parameters = sc.loadjson(fname)
    if not isinstance(parameters, dict):
        raise TypeError(f'Uploaded file was a {type(parameters)} object rather than a dict')
    if  'sim_pars' not in parameters or 'epi_pars' not in parameters:
        raise KeyError(f'Parameters file must have keys "sim_pars" and "epi_pars", not {parameters.keys()}')
    return parameters


@app.register_RPC(call_type='upload')
def upload_file(file):
    stem, ext = os.path.splitext(file)
    fd, path = tempfile.mkstemp(suffix=ext, prefix="input_", dir=tempfile.mkdtemp())
    sh.copyfile(file, path)
    return path


@app.register_RPC()
def get_gantt(int_pars=None, intervention_config=None):
    df = []
    response = {'id': 'test'}
    for key,scenario in int_pars.items():
        for timeline in scenario:
            task = intervention_config[key]['formTitle']
            level = task + ' ' + str(timeline.get('level', ''))
            df.append(dict(Task=task, Start=timeline['start'], Finish=timeline['end'], Level= level))
    if len(df) > 0:
        fig = ff.create_gantt(df, height=400, index_col='Level', title='Intervention timeline',
                            show_colorbar=True, group_tasks=True, showgrid_x=True, showgrid_y=True)
        fig.update_xaxes(type='linear')
        response['json'] = fig.to_json()

    return response


#%% Define the core API

def parse_interventions(int_pars):
    '''
    Parse interventions. Format

    Args:
        int_pars = {
            'social_distance': [
                {'start': 1,  'end': 19, 'level': 'aggressive'},
                {'start': 20, 'end': 30, 'level': 'mild'},
                ],
            'school_closures': [
                {'start': 12, 'end': 14}
                ],
            'symptomatic_testing': [
                {'start': 8, 'end': 25, 'level': 60}
                ]}

    '''
    intervs = []

    if int_pars is not None:
        masterlist = []
        for ikey,intervlist in int_pars.items():
            for iconfig in intervlist:
                iconfig['ikey'] = ikey
                masterlist.append(dict(iconfig))

        for iconfig in masterlist:
            ikey  = iconfig['ikey']
            start = iconfig['start']
            end   = iconfig['end']
            if ikey == 'social_distance':
                level = iconfig['level']
                mapping = {
                    'mild': 0.8,
                    'moderate': 0.5,
                    'aggressive': 0.2,
                    }
                change = mapping[level]
                interv = cv.change_beta(days=[start, end], changes=[change, 1.0])
            elif ikey == 'school_closures':
                change = 0.7
                interv = cv.change_beta(days=[start, end], changes=[change, 1.0], layers='a')
            elif ikey == 'symptomatic_testing':
                level = iconfig['level']
                level = float(level)/100
                asymp_prob = 0.0
                delay = 0.0
                interv = cv.test_prob(start_day=start, end_day=end, symp_prob=level, asymp_prob=asymp_prob, test_delay=delay)
            elif ikey == 'contact_tracing':
                trace_prob = {'a':1.0}
                trace_time = {'a':0.0}
                interv = cv.contact_tracing(start_day=start, end_day=end, trace_probs=trace_prob, trace_time=trace_time)
            else:
                raise NotImplementedError

            intervs.append(interv)

    return intervs


@app.register_RPC()
def run_sim(sim_pars=None, epi_pars=None, int_pars=None, datafile=None, show_animation=False, n_days=90, location=None, verbose=True, die=die):
    ''' Create, run, and plot everything '''
    errs = []
    try:
        # Fix up things that JavaScript mangles
        orig_pars = cv.make_pars(set_prognoses=True, prog_by_age=False, use_layers=False)

        defaults = get_defaults(merge=True)
        web_pars = {}
        web_pars['verbose'] = verbose # Control verbosity here

        for key,entry in {**sim_pars, **epi_pars}.items():
            print(key, entry)

            best   = defaults[key]['best']
            minval = defaults[key]['min']
            maxval = defaults[key]['max']

            try:
                web_pars[key] = np.clip(float(entry['best']), minval, maxval)
            except Exception as E:
                user_key = entry['name']
                user_val = entry['best']
                err = f'Could not convert parameter "{user_key}" from value "{user_val}"; using default value instead.'
                errs.append(log_err(err, E))
                web_pars[key] = best
                if die: raise

            if key in sim_pars:
                sim_pars[key]['best'] = web_pars[key]
            else:
                epi_pars[key]['best'] = web_pars[key]

        # Convert durations
        web_pars['dur'] = sc.dcp(orig_pars['dur']) # This is complicated, so just copy it
        web_pars['dur']['exp2inf']['par1']  = web_pars.pop('web_exp2inf')
        web_pars['dur']['inf2sym']['par1']  = web_pars.pop('web_inf2sym')
        web_pars['dur']['crit2die']['par1'] = web_pars.pop('web_timetodie')
        web_dur = web_pars.pop('web_dur')
        for key in ['asym2rec', 'mild2rec', 'sev2rec', 'crit2rec']:
            web_pars['dur'][key]['par1'] = web_dur

        # Add n_days
        web_pars['n_days'] = n_days

        # Add demographic
        web_pars['location'] = location

        # Add the intervention
        web_pars['interventions'] = parse_interventions(int_pars)

        # Handle CFR -- ignore symptoms and set to 1
        web_pars['prognoses'] = sc.dcp(orig_pars['prognoses'])
        web_pars['rel_symp_prob']   = 1e4 # Arbitrarily large
        web_pars['rel_severe_prob'] = 1e4
        web_pars['rel_crit_prob']   = 1e4
        web_pars['prognoses']['death_probs'][0] = web_pars.pop('web_cfr')
        if web_pars['rand_seed'] == 0:
            web_pars['rand_seed'] = None
        web_pars['timelimit'] = max_time  # Set the time limit
        web_pars['pop_size'] = int(web_pars['pop_size'])  # Set data type
        web_pars['contacts'] = int(web_pars['contacts'])  # Set data type

    except Exception as E:
        errs.append(log_err('Parameter conversion failed!', E))
        if die: raise

    # Create the sim and update the parameters
    try:
        sim = cv.Sim(pars=web_pars,datafile=datafile)
    except Exception as E:
        errs.append(log_err('Sim creation failed!', E))
        if die: raise

    if verbose:
        print('Input parameters:')
        print(web_pars)

    # Core algorithm
    try:
        sim.run(do_plot=False)
    except TimeoutError as TE:
        err = f"The simulation stopped on day {sim.t} because run time limit ({sim['timelimit']} seconds) was exceeded. Please reduce the population size and/or number of days simulated."
        errs.append(log_err(err, TE))
        if die: raise
    except Exception as E:
        errs.append(log_err('Sim run failed!', E))
        if die: raise

    # Core plotting
    def process_graphs(figs):
        jsons = []
        for fig in sc.promotetolist(figs):
            fig.update_layout(paper_bgcolor=bgcolor, plot_bgcolor=plotbg)
            output = {'json': fig.to_json(), 'id': str(sc.uuid())}
            d = json.loads(output['json'])
            d['config'] = {'responsive': True}
            output['json'] = json.dumps(d)
            jsons.append(output)
        return jsons

    graphs = []
    try:
        graphs += process_graphs(cv.standard_plots(sim))
        graphs += process_graphs(cv.plot_people(sim))
        if show_animation:
            graphs += process_graphs(cv.animate_people(sim))
    except Exception as E:
        errs.append(log_err('Plotting failed!', E))
        if die: raise

    # Create and send output files (base64 encoded content)
    try:
        files,summary = get_output_files(sim)
    except Exception as E:
        files = {}
        summary = {}
        errs.append(log_err('Unable to save output files!', E))
        if die: raise

    output = {}
    output['errs']     = errs
    output['sim_pars'] = sim_pars
    output['epi_pars'] = epi_pars
    output['int_pars'] = int_pars
    output['graphs']   = graphs
    output['files']    = files
    output['summary']  = summary

    return output



def get_output_files(sim):
    ''' Create output files for download '''

    datestamp = sc.getdate(dateformat='%Y-%b-%d_%H.%M.%S')
    ss = sim.to_excel()

    files = {}
    files['xlsx'] = {
        'filename': f'covasim_results_{datestamp}.xlsx',
        'content': 'data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,' + base64.b64encode(ss.blob).decode("utf-8"),
    }

    json_string = sim.to_json(verbose=False)
    files['json'] = {
        'filename': f'covasim_results_{datestamp}.json',
        'content': 'data:application/text;base64,' + base64.b64encode(json_string.encode()).decode("utf-8"),
    }

    # Summary output
    summary = {
        'days': sim.npts-1,
        'cases': round(sim.results['cum_infections'][-1]),
        'deaths': round(sim.results['cum_deaths'][-1]),
    }
    return files, summary


#%% Run the server using Flask

if __name__ == "__main__":

    os.chdir(sc.thisdir(__file__))

    if len(sys.argv) > 1:
        app.config['SERVER_PORT'] = int(sys.argv[1])
    else:
        app.config['SERVER_PORT'] = 8188
    if len(sys.argv) > 2:
        autoreload = int(sys.argv[2])
    else:
        autoreload = 1

    app.run(autoreload=autoreload)
