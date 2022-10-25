"""
Download images linked in dataset

This processor also requires ffmpeg to be installed in 4CAT's backend
https://ffmpeg.org/
"""
import json
import os
import shutil
import zipfile

from videohash import VideoHash
from videohash.exceptions import FFmpegNotFound

from backend.abstract.processor import BasicProcessor
from common.lib.exceptions import ProcessorInterruptedException
from common.lib.user_input import UserInput

__author__ = "Dale Wahl"
__credits__ = ["Dale Wahl"]
__maintainer__ = "Dale Wahl"
__email__ = "4cat@oilab.eu"


class VideoHasher(BasicProcessor):
	"""
	Video Hasher

	Converts videos into 64 bit hashes which can be used to identify near duplicate videos. The accuracy of these hashes
	can very greatly depending on the number of frames per second collected from each video.

	After creating an image collage from the collected video frames, the videohash library relies on two main aspects:
	Discrete Wavelet Transformation of the image (https://en.wikipedia.org/wiki/Discrete_wavelet_transform) and the
	dominant color. The collage being divided into 64 "images" or "pixels" and ultimately defined by those two aspects
	(one "image"/"pixel" per bit).

	Increasing the frames per second has proven necessary for short videos (if there are ultimately less than 64 frames,
	there will essentially be black frames that will be shared with every other video with less than 64 frames). It also
	seems necessary to collect the same frames per second for comparison between videos as variation in this will cause
	different frames to be collected per video (more testing needs to be done here). Additionally, short videos often
	do not have much differentiating information particularly if there is little difference between frames (i.e. no
	"scene" changes) and have lead to unwanted collision in tests.
	"""
	type = "video-hashes"  # job type ID
	category = "Visual"  # category
	title = "Identify near duplicate videos"  # title displayed in UI
	description = "IN DEVELOPMENT: Creates video hashes (64 bits; identifiers) to identify near duplicate videos in a dataset. Only utilizes the video and does not consider audio (see references). This process can take a long time depending on video length, amount, and frames per second."  # description displayed in UI
	extension = "ndjson"  # extension of result file, used internally and in UI

	references = [
		"[Video Hash](https://github.com/akamhy/videohash#readme)",
	]

	options = {
		"frame_interval": {
			"type": UserInput.OPTION_TEXT,
			"help": "Number of frames extracted per second to extract from video",
			"tooltip": "The default value is 1 frame per second. For 1 frame per 5 seconds pass 0.2 (1/5). For 5 fps pass 5. For short videos, more frames per second lead to less collision (unsimilar videos being marked as similar), but require more time (2 fps is double the time of 1 fps).",
			"coerce_type": float,
			"default": 1,
			"min": 0,
			"max": 5,
		},
		"store_video_collages": {
			"type": UserInput.OPTION_TOGGLE,
			"help": "Store video collages",
			"default": False,
			"tooltip": "If enabled, the video collages will be stored. A seperate processor can be used to download them."
		},
		"update_parent": {
			"type": UserInput.OPTION_TOGGLE,
			"help": "Add video hashes to parent dataset",
			"default": False,
			"tooltip": "If enabled, the latest created video hashes will be added to the parent dataset and thus can be exported along with originating posts/items. This will overwrite previously created video_hashes added to dataset."
		},
	}

	@classmethod
	def is_compatible_with(cls, module=None):
		"""
		Allow on tiktok-search only for dev
		"""
		return module.type == "video-downloader"

	def process(self):
		"""
		This takes a zipped set of videos, uses https://pypi.org/project/videohash/ and https://ffmpeg.org/ to collect
		frames from the videos at intervals and create image collages to hashes for comparison of videos.
		"""
		# Check processor able to run
		if self.source_dataset.num_rows == 0:
			self.dataset.update_status("No videos to compare.", is_final=True)
			self.dataset.finish(0)
			return

		# Collect parameters
		frame_interval = self.parameters.get("frame_interval", 1)
		self.dataset.log('Frames per seconds: %f' % frame_interval)
		store_video_collages = self.parameters.get("store_video_collages", False)
		update_parent = self.parameters.get("update_parent", False)

		self.dataset.update_status("Extract all videos")
		# TEST: There is a quality issue when iteratiting via iterate_archive_contents
		staging_area = self.unpack_archive_contents(self.source_file)
		video_filenames = os.listdir(staging_area)

		# Prepare staging area for videos and video tracking
		# staging_area = self.dataset.get_staging_area()
		self.dataset.log('Staging directory location: %s' % staging_area)
		if store_video_collages:
			staging_area.joinpath('collages/').mkdir(exist_ok=True)

		video_hashes = {}
		video_metadata = None
		total_possible_videos = self.source_dataset.num_rows - 1  # for the metadata file that is included in archives
		processed_videos = 0

		self.dataset.update_status("Creating video hashes")
		# for path in self.iterate_archive_contents(self.source_file, staging_area):
		for path in video_filenames:
			path = staging_area.joinpath(path)
			if self.interrupted:
				raise ProcessorInterruptedException("Interrupted while determining image wall order")

			if path.name == '.metadata.json':
				# Keep it and move on
				with open(path) as file:
					video_metadata = json.load(file)
				continue

			try:
				videohash = VideoHash(path=str(path), storage_path=str(staging_area), frame_interval=frame_interval, do_not_copy=True)
			except FFmpegNotFound:
				self.log.error('ffmpeg must be installed for video_hash.py processor to be used.')
				self.dataset.update_status("FFmpeg software not found. Please contact 4CAT maintainers.", is_final=True)
				self.dataset.finish(0)
				return
			except UnicodeDecodeError as e:
				# This seems to occur randomly and can be resolved by retrying
				error = 'Error with video %s (%s): Retrying...' % (str(path), str(e))
				self.dataset.log(error)
				try:
					videohash = VideoHash(path=str(path), storage_path=str(staging_area), frame_interval=frame_interval, do_not_copy=True)
				except UnicodeDecodeError as e:
					error = 'Error repeated with video %s: %s' % (str(path), str(e))
					self.dataset.log(error)
					video_hashes[path.name] = {'error': error}
					continue

			# except Exception as e:
			# 	error = 'Error with video %s: %s' % (str(path), str(e))
			# 	self.dataset.log(error)
			# 	video_hashes[path.name] = {'error': error}
			# 	continue


			video_hashes[path.name] = {'videohash': videohash}

			# if store_video_collages:
			# 	shutil.copy(videohash.collage_path, staging_area.joinpath('collages/' + path.stem + '.jpg'))
			# 	# videohash.image.save(staging_area.joinpath('collages/' + path.stem + '.jpg'))
			# 	video_hashes[path.name]['video_collage_filename'] = path.stem + '.jpg'

			# Remove temp files used to make video hash
			# videohash.delete_storage_path()

			processed_videos += 1
			self.dataset.update_status(
				"Created %i/%i video hashes" % (processed_videos, total_possible_videos))
			self.dataset.update_progress(processed_videos / total_possible_videos)

		if store_video_collages:
			# Pack up the video collages somewhere we can retrieve them later
			# TODO: This would be better as its own processor, but creating the collages twice seems counterproductive
			# Perhaps we can instead somehow utilize the processor pipeline to feed results into a second processor?
			self.dataset.update_status("Compressing video collages into archive")
			# files = staging_area.glob("*")
			# files = staging_area.joinpath('collages/').glob("*")
			done = 0
			# Update suffix for zipped images
			video_collage_results_path = self.dataset.get_results_path().with_suffix('.zip')
			self.dataset.log('Video collages stored here: %s' % video_collage_results_path)
			with zipfile.ZipFile(video_collage_results_path, "w", compression=zipfile.ZIP_STORED) as zip:
				# setup file paths variable
				filePaths = []

				# Read all directory, subdirectories and file lists
				for root, directories, files in os.walk(staging_area):
					for filename in files:
						# Create the full filepath by using os module.
						filePath = os.path.join(root, filename)
						filePaths.append(filePath)

				for output_path in filePaths:
					zip.write(output_path)
					done += 1
			# This should be taken care of in after_process()
			shutil.rmtree(staging_area)
			self.dataset.update_status("Compressed %i video collages into archive" % done)

		# Write output file
		num_posts = 0
		post_id_to_results = {}
		if video_metadata is None:
			# Not good, but let's store the video_hashes and note the error
			self.dataset.update_status("Error connecting video hashes to original dataset", is_final=True)
			with self.dataset.get_results_path().open("w", encoding="utf-8", newline="") as outfile:
				for filename, data in video_hashes.items():
					video_hash = data.get('videohash')
					item = {
						'filename': filename,
						'video_hash': video_hash.hash,
						'video_duration': video_hash.video_duration,
						'video_collage_filename': data.get('video_collage_filename'),
					}
					outfile.write(json.dumps(item) + "\n")
					num_posts += 1
		else:
			self.dataset.update_status("Saving video hash results")
			with self.dataset.get_results_path().open("w", encoding="utf-8", newline="") as outfile:
				for url, data in video_metadata.items():
					if video_hashes[data.get('filename')].get('error'):
						data.update({
								'url': url,
								'error': video_hashes[data.get('filename')].get('error'),
							})
					else:
						video_hash = video_hashes[data.get('filename')].get('videohash')
						data.update({
							'url': url,
							'video_hash': video_hash.hash,
							'video_duration': video_hash.video_duration,
							'video_collage_filename': video_hashes[data.get('filename')].get('video_collage_filename'),
						})

						if update_parent:
							for post_id in data.get('post_ids', []):
								# Posts can have multiple videos
								if post_id in post_id_to_results.keys():
									post_id_to_results[post_id].append((url, video_hash.hash))
								else:
									post_id_to_results[post_id] = [(url, video_hash.hash)]

					outfile.write(json.dumps(data) + "\n")
					num_posts += 1

		if update_parent and video_metadata:
			updated_rows = []
			for post in self.dataset.top_parent().iterate_items(self):
				video_hashes = post_id_to_results.get(post.get('id'), [])

				# Combine multiple hashes along with specific url
				updated_rows.append(','.join(['%s: %s' % (url, video_hash) for url, video_hash in video_hashes]))

			self.add_field_to_parent(field_name='4CAT_video_hashes',
									 new_data=updated_rows,
									 which_parent=self.dataset.top_parent(),
									 update_existing=True)

		# Finish up
		self.dataset.update_status(
			'Created %i video hashes%s.' % (num_posts, ' and stored video collages' if store_video_collages else ''))
		self.dataset.finish(num_posts)
