""" Analytics Framework Module

This module contains the Analytics_Framework class, which provides methods for importing, exporting, and analyzing EEG data.

Author: Janik Michael Müller
Date: 15.02.2024
Version: 1.0
"""

import numpy as np
import mne, re, os
from mne_bids import BIDSPath, write_raw_bids, read_raw_bids
from scipy.stats import pearsonr
from scipy.signal import firls
from FACET.EEG_obj import EEG
import numpy as np
from loguru import logger
import scipy.io as sio

# import inst for mne python


class Analytics_Framework:
    def __init__(self, FACET, eeg = None):
        self._loaded_triggers = None
        self._plot_number = 0
        self._FACET = FACET

        if eeg:
            self._eeg=eeg
        else:
            self._eeg = EEG()


    def import_EEG(self, path, artifact_to_trigger_offset=0, upsampling_factor=10, fmt="edf", bads=[], subject="subjectid", session="sessionid", task="corrected"):
            """
            Imports EEG data from a file.

            Parameters:
            - filename (str): The path to the EEG file.
            - artifact_to_trigger_offset (float): The relative position of the trigger in the data.
            - upsampling_factor (int): The factor by which to upsample the data.
            - fmt (str): The format of the EEG file (either "edf" or "gdf").
            - bads (list): A list of bad channels to exclude from the data.

            Returns:
            - dict: A dictionary containing the imported EEG data and relevant information.
            """
            if fmt == "edf":
                raw = mne.io.read_raw_edf(path)
            elif fmt == "gdf":
                raw = mne.io.read_raw_gdf(path)
            elif fmt == "bids":
                bids_path_i = BIDSPath(subject=subject, session=session, task=task, root=path)
                raw = read_raw_bids(bids_path_i)
            else:
                raise ValueError("Format not supported")
            raw.load_data()

            all_channels = raw.ch_names
            exclude = [item for i, item in enumerate(all_channels) if item in bads]
            raw.info["bads"] = exclude
            data_time_start = raw.times[0]
            data_time_end = raw.times[-1]

            self._eeg = EEG(mne_raw=raw,
                            estimated_noise=np.zeros(raw._data.shape),
                            artifact_to_trigger_offset=artifact_to_trigger_offset,
                            upsampling_factor=upsampling_factor,
                            data_time_start=data_time_start,
                            data_time_end=data_time_end)
            events = self._try_to_get_events()
            if events is not None:
                self._eeg.all_events = events
            if fmt == "bids":
                self._eeg.BIDSPath = bids_path_i
            logger.debug("Importing EEG with:")
            logger.debug("Channels " + str(raw.ch_names))
            logger.debug(f"Time Start: {data_time_start}s")
            logger.debug(f"Time End: {data_time_end}s")
            logger.debug(f"Number of Samples: {raw.n_times}")
            logger.debug(f"Sampling Frequency: {raw.info['sfreq']}Hz")
            logger.debug(path)
            return self._eeg

    def export_EEG(self, path, fmt="edf", subject="subjectid", session="sessionid", task="corrected", event_id=None):
        if fmt == "bids":
            _BIDSPath = BIDSPath(
                subject=subject, session=session, task=task, root=path
            )
            logger.info("Exporting Channels: "+str(self._eeg.mne_raw.ch_names))

            raw = self._eeg.mne_raw.copy()
            #drop stim channels
            stim_channels = mne.pick_types(raw.info, meg=False, eeg=False, stim=True)
            raw.drop_channels([raw.ch_names[ch] for ch in stim_channels])

            if self._eeg.mne_raw is not None:
                write_raw_bids(raw=raw, bids_path=_BIDSPath, overwrite=True, format="EDF", allow_preload=True, events=self._eeg.triggers_as_events, event_id=event_id)
        else:
            raw = self._eeg.mne_raw
            raw.export(path, fmt=fmt, overwrite=True)

    def find_triggers(self, regex):
        """
            Find triggers in the raw EEG data based on a regular expression.

            Args:
                regex (str): Regular expression pattern to match against trigger values.

            Returns:
                None
        """
        raw = self._eeg.mne_raw
        stim_channels = mne.pick_types(raw.info, meg=False, eeg=False, stim=True)
        events=[]
        filtered_events=[]
        

        if len(stim_channels) > 0:
            logger.debug("Stim-Kanäle gefunden:", [raw.ch_names[ch] for ch in stim_channels])
            events = mne.find_events(raw, stim_channel=raw.ch_names[stim_channels[0]], initial_event=True)
            pattern = re.compile(regex)
            filtered_events = [event for event in events if pattern.search(str(event[2]))]

        else:
            logger.debug("No Stim-Channels found.")
            events_obj = mne.events_from_annotations(raw)
            logger.debug(events_obj[1])
            filtered_events = mne.events_from_annotations(raw, regexp=regex)[0]
        
        if len(filtered_events)==0:
            logger.error("No events found!")
            return
        filtered_positions = [event[0] for event in filtered_events]
        triggers = filtered_positions
        logger.debug(f"Found {len(triggers)} triggers")
        self._eeg.last_trigger_search_regex=regex
        self._eeg.loaded_triggers = triggers

        self.derive_parameters()

    def derive_parameters(self):
        triggers = self._eeg.loaded_triggers
        time_first_artifact_start = self._eeg.mne_raw.times[triggers[0]]
        time_last_trigger = self._eeg.mne_raw.times[triggers[-1]]
        self._eeg.time_first_artifact_start = time_first_artifact_start + self._eeg.artifact_to_trigger_offset
        self._check_volume_gaps()
        self._derive_art_length()
        self._eeg.time_last_artifact_end = time_last_trigger + self._eeg.artifact_to_trigger_offset + self._eeg.artifact_duration
        self._derive_anc_hp_params()
        self._eeg._tmin = self._eeg.artifact_to_trigger_offset
        self._eeg._tmax = self._eeg.artifact_to_trigger_offset + self._eeg.artifact_duration

    def get_mne_raw(self):
            """
            Returns the raw EEG data.

            Returns:
                mne.io.Raw: The raw EEG data.
            """
            return self._eeg.mne_raw

    def get_mne_raw_orig(self):
        """
        Returns the original raw EEG data.

        Returns:
            mne.io.Raw: The original raw EEG data.
        """
        return self._eeg.mne_raw_orig
    
    def get_eeg(self):
            """
            Returns the EEG data associated with this instance.

            Returns:
                The EEG data.
            """
            return self._eeg

    def plot_EEG(self, start = 0):
        self._plot_number += 1
        self._raw.plot(title=str(self._plot_number), start=start)

    def _try_to_get_events(self):
        """
        Tries to retrieve the events from the raw EEG data.

        Returns:
            events (ndarray or None): The events extracted from the raw EEG data, or None if no events are found.
        """
        # Check if there are annotations and convert
        if self._eeg.mne_raw.annotations:
            return mne.events_from_annotations(self._eeg.mne_raw)[0]
        
        # Check if there are events
        if hasattr(self._eeg.mne_raw, "events"):
            return self._eeg.mne_raw.events
        
        return None

    def _derive_art_length(self):
        """
        Calculate the length of an artifact based on trigger distances.

        This method calculates the length of an artifact by analyzing the trigger distances
        between consecutive triggers in the EEG data. If there are volume gaps in the data,
        the middle distance is used to determine the trigger distances belonging to slice triggers.
        Otherwise, all trigger distances are considered.

        The calculated artifact length is stored in the `_eeg.artifact_length` attribute.

        If there are no volume gaps, the duration of the artifact is also calculated and stored
        in the `_eeg.artifact_duration` attribute.

        Returns:
            None
        """
        d = np.diff(self._eeg.loaded_triggers)  # trigger distances

        if self._eeg.volume_gaps:
            m = np.mean([np.min(d), np.max(d)])  # middle distance
            ds = d[d < m]  # trigger distances belonging to slice triggers
            # dv = d[d > m]  # trigger distances belonging to volume triggers

            # total length of an artifact
            self._eeg.artifact_length = np.max(ds)  # use max to avoid gaps between slices
            
        else:
            # total length of an artifact
            self._eeg.artifact_length = np.max(d)
        self._eeg.artifact_duration = self._eeg.artifact_length / self._eeg.mne_raw.info["sfreq"]

    def add_triggers(self, triggers):
        """
        Add triggers to the EEG data.

        This method adds triggers to the EEG data based on the provided trigger positions.

        Args:
            triggers (list): List of trigger positions.

        Returns:
            None
        """
        if len(triggers) == 0:
            logger.error("No triggers provided!")
            return
        #check if triggers are within the data
        if triggers[0] < 0 or triggers[-1] > self._eeg.mne_raw.n_times:
            logger.error("Triggers are not within the data!")
            return
        
        #check if triggers are not already in the data
        intersection = np.intersect1d(triggers, self._eeg.loaded_triggers)
        if len(intersection) > 0:
            logger.warning(f"There are {len(intersection)} triggers already in the data at positions {intersection}. Removing them...")
            triggers = np.setdiff1d(triggers, intersection).tolist()
            return
                    
        #add triggers and ensure they are sorted
        self._eeg.loaded_triggers = np.sort(np.concatenate([self._eeg.loaded_triggers, triggers])).tolist()

        self.derive_parameters()

    def find_missing_triggers(self, mode="auto", ref_channel=0):
        """
        Find missing triggers in the EEG data.

        This method finds missing triggers in the EEG data based on the calculated artifact length.

        Returns:
            None
        """
        missing_triggers = []
        if mode == "auto":
            search_window = int(0.5 * self._eeg.artifact_length)
            logger.info("Finding missing triggers using auto mode...")
            if self._eeg.volume_gaps:
                logger.warning("Volume gaps are detected or flag is manually set to True. Results may be inaccurate")
            logger.debug("Generating template from reference channel...")
            _3d_matrix = self._FACET._correction.calc_matrix_AAS(channels=[ref_channel])
            template = self._FACET._correction.calc_avg_artifact(_3d_matrix)[0][0]
            # iterate through the trigger positions check for each trigger if the next trigger is within the artifact length with a threshold of 1.9*artifactlength
            logger.debug("Checking holes in the trigger positions...")
            for i in range(len(self._eeg.loaded_triggers) - 1):
                if self._eeg.loaded_triggers[i + 1] - self._eeg.loaded_triggers[i] > self._eeg.artifact_length *1.9:
                    new_trigger = self._FACET._correction._align_trigger(self._eeg.loaded_triggers[i] + self._eeg.artifact_length, template, search_window, ref_channel)
                    missing_triggers.append(new_trigger)
    
            logger.debug(f"Found {len(missing_triggers)} missing triggers")
            logger.debug("Now removing triggers that are not artifacts...")
            #now check if each missing trigger is an artifact and remove if it is not
            for trigger in missing_triggers:
                if not self._is_artifact(trigger, template):
                    missing_triggers.remove(trigger)
            logger.debug(f"Found {len(missing_triggers)} missing triggers that are artifacts")
            logger.debug("Now adding missing triggers at the beginning and end of the data...")
            #now check iteratively if triggers are missing at the beginning and end of the data by checking if first trigger - artifact length is an artifact and if last trigger + artifact length is an artifact
            # adding at the beginning and the end as long as the triggers are artifacts
            temp_pos = self._eeg.loaded_triggers[0] - self._eeg.artifact_length
            new_pos = self._FACET._correction._align_trigger(temp_pos, template, search_window, ref_channel)
            count = 0
            while self._is_artifact(new_pos, template):
                missing_triggers.insert(0, new_pos)
                count += 1
                temp_pos = new_pos - self._eeg.artifact_length
                new_pos = self._FACET._correction._align_trigger(temp_pos, template, search_window, ref_channel)
            logger.debug(f"Found {count} missing triggers at the beginning of the data")
            count = 0
            temp_pos = self._eeg.loaded_triggers[-1] + self._eeg.artifact_length
            new_pos = self._FACET._correction._align_trigger(temp_pos, template, search_window, ref_channel)
            while self._is_artifact(new_pos, template):
                missing_triggers.append(new_pos)
                count += 1
                temp_pos = new_pos + self._eeg.artifact_length
                new_pos = self._FACET._correction._align_trigger(temp_pos, template, search_window, ref_channel)
            logger.debug(f"Found {count} missing triggers at the end of the data")
            logger.debug(f"Found {len(missing_triggers)} missing triggers in total")
            if len(missing_triggers) == 0:
                logger.info("No missing triggers found. Finishing...")
                return []
            logger.debug("Now aligning the missing triggers...")
            # align all missing triggers with self._FACET._correction._align_trigger
            for i in range(len(missing_triggers)):
                missing_triggers[i] = self._FACET._correction._align_trigger(missing_triggers[i], template, search_window, ref_channel)
            # add the missing triggers as annotations with description "missing_trigger"
            on_sets = np.array(missing_triggers) / self._eeg.mne_raw.info["sfreq"]
            #zero duration
            durations = np.zeros(len(missing_triggers))
            descriptions = ["missing_trigger"] * len(missing_triggers)
            annotations = mne.Annotations(onset=on_sets, duration=durations, description=descriptions)
            self._add_annotations(annotations)
            # add the missing triggers to the EEG data
            self.add_triggers(missing_triggers)
        else:
            logger.error("Mode not supported!")
        return missing_triggers
    def _add_annotations(self, annotations):
        """
        Add annotations to the EEG data.

        This method adds annotations to the EEG data based on the provided annotations.
        
        Args:
            annotations (list): List of annotations to add.

        Returns:
            None
        """
        raw = self._eeg.mne_raw
        # Hole die bestehenden Annotations aus dem Raw-Objekt
        existing_annotations = raw.annotations

        # Kombiniere die bestehenden mit den neuen Annotationen
        # Dazu fügst du die Listen von Onsets, Durations und Descriptions zusammen
        combined_onset = list(existing_annotations.onset) + list(annotations.onset)
        combined_duration = list(existing_annotations.duration) + list(annotations.duration)
        combined_description = list(existing_annotations.description) + list(annotations.description)

        # Erstelle ein neues Annotations-Objekt mit den kombinierten Daten
        combined_annotations = mne.Annotations(onset=combined_onset,
                                                duration=combined_duration,
                                                description=combined_description)

        # Setze die kombinierten Annotations zurück in das Raw-Objekt
        raw.set_annotations(combined_annotations)
    def _is_artifact(self, position, template, threshold=0.9):
        """
        Check if a given position mark an artifact.

        This method checks if a given position based on a template with a correlation threshold of 0.9

        Args:
            position (int): The position to check.

        Returns:
            bool: True if the position is an artifact, False otherwise.
        """
        new_position = self._FACET._correction._align_trigger(position, template, 3*self._eeg.upsampling_factor, 0)
        smin = int(self._eeg.get_tmin()*self._eeg.mne_raw.info["sfreq"])
        data = self._eeg.mne_raw.get_data(start=new_position+smin, stop=new_position+smin+self._eeg.artifact_length)
        template = template[:len(data[0])]
        if len(template) < 3:
            return False
        corr = np.abs(pearsonr(data[0], template)[0])
        return corr > threshold
        


    def _derive_anc_hp_params(self):
        """
        Derive ANC high-pass filter parameters.

        This method derives the parameters for the ANC high-pass filter based on the trigger frequency
        and the sampling frequency of the EEG data. The filter order is calculated based on the
        artifact length and the upsampling factor.

        The calculated filter weights are stored in the `_eeg.anc_hp_filter_weights` attribute.

        Returns:
            None
        """

        sfreq = self._eeg.mne_raw.info["sfreq"]
        artifact_length = self._eeg.artifact_length
        trans = 0.15
        nyq = 0.5 * sfreq

        if self._eeg.count_triggers >= 1:
            # Schätzung der Frequenz der Trigger
            Tr = 1
            while Tr <= self._eeg.count_triggers:
                tr_samp_diff = self._eeg.loaded_triggers[Tr] - self._eeg.loaded_triggers[0]  # Python-Indexierung beginnt bei 0
                if tr_samp_diff >= sfreq:
                    break
                Tr += 1
            # ANC HP cut-off Frequenz ist 25% niedriger als die geschätzte Triggerfrequenz
            self._eeg.anc_hp_frequency = 0.75 * Tr
        else:
            self._eeg.anc_hp_frequency = 2

        filtorder = round(1.2 * sfreq / (self._eeg.anc_hp_frequency * (1 - trans)))
        if filtorder % 2 == 0:
            filtorder += 1


        f = [0, self._eeg.anc_hp_frequency * (1 - trans) / nyq, self._eeg.anc_hp_frequency / nyq, 1]
        a = [0, 0, 1, 1]
        self._eeg.anc_hp_filter_weights = firls(filtorder, f, a)
        # load the filter weights from mat file
        #self._eeg.anc_hp_filter_weights = sio.loadmat('FilterWeights.mat')['filtWeights'][0]
        self._eeg.anc_filter_order = artifact_length
        
    def _check_volume_gaps(self):
        # Due to asynchronous sampling the distances might vary a bit. We
        # accept one mean value, plus and minus one (gives a range of 2),
        # plus one more to be a bit more robust.
        if self._eeg.volume_gaps is None:
            if np.ptp(np.diff(self._eeg.loaded_triggers)) > 3:
                self._eeg.volume_gaps = True
            else:
                self._eeg.volume_gaps = False

    def _filter_annotations(self, regex):
            """Extract specific annotations from an MNE Raw object.

            Args:
                regex (str): Regular expression pattern to match the annotation description.

            Returns:
                list: List of tuples containing the matched annotations (time, duration, description).
            """
            eeg = self._eeg
            # initialize list to store results
            specific_annotations = []

            # compile the regular regex pattern
            pattern = re.compile(regex)

            # loop through each annotation in the raw object
            for annot in eeg.mne_raw.annotations:
                # check if the annotation description matches the pattern
                if pattern.search(annot["description"]):
                    # if it does, append the annotation (time, duration, description) to our results list
                    specific_annotations.append(
                        (annot["onset"], annot["duration"], annot["description"])
                    )

            return specific_annotations
    
    def print_analytics(self):
        """
        Prints analytics information.

        This method logs various analytics information, including the number of triggers found,
        art length, duration of art in seconds, number of channels, and channel names.

        """
        logger.info("Analytics:")
        logger.info(f"Number of Triggers found: {self._eeg.count_triggers}")
        logger.info(f"Art Length: {self._eeg.artifact_length}")
        logger.info(f"Duration of Art in seconds: {self._eeg.artifact_duration}")

        # EEG information
        # print EEG Channels
        raw = self._eeg.mne_raw
        ch_names = raw.ch_names
        count_ch = len(ch_names)
        logger.info("Time Start: " + str(raw.times[0]) + " s")
        logger.info("Time End: " + str(raw.times[-1]) + " s")
        logger.info("Sampling Frequency: " + str(raw.info["sfreq"]) + " Hz")
        logger.info("Number of Samples: " + str(raw.n_times))
        logger.info("Number of Channels: " + str(count_ch) )
        logger.info("Channel Names: " + str(ch_names))

